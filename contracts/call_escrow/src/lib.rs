//! CallEscrow: per-minute payment for live video guidance.
//!
//! Lifecycle of a call session:
//!   open    -> user locks a budget in the contract
//!   close   -> either party hangs up; cost is computed from ledger time,
//!              the unused budget is refunded, earnings enter a hold
//!   confirm -> user releases the guide's earnings early
//!   report  -> user disputes the call while the hold is running
//!   resolve -> admin splits disputed earnings after human review
//!   claim   -> guide withdraws earnings once the hold is over
#![no_std]

use soroban_sdk::{
    contract, contracterror, contractevent, contractimpl, contracttype, panic_with_error, token,
    Address, BytesN, Env,
};

const DAY_LEDGERS: u32 = 17_280;
const TTL_THRESHOLD: u32 = 30 * DAY_LEDGERS;
const TTL_EXTEND_TO: u32 = 60 * DAY_LEDGERS;
const BPS_DENOMINATOR: i128 = 10_000;

#[contracttype]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Status {
    Open,
    Held,
    Disputed,
    Released,
    Resolved,
}

#[contracttype]
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Session {
    pub user: Address,
    pub guide: Address,
    pub rate_per_minute: i128,
    pub budget: i128,
    pub started_at: u64,
    pub ended_at: u64,
    pub earned: i128,
    pub status: Status,
    pub recording_hash: Option<BytesN<32>>,
}

#[contracttype]
#[derive(Clone)]
enum DataKey {
    Admin,
    Token,
    HoldSeconds,
    FeeBps,
    NextId,
    Guide(Address),
    Session(u64),
}

#[contracterror]
#[derive(Copy, Clone, Debug, Eq, PartialEq, PartialOrd, Ord)]
#[repr(u32)]
pub enum Error {
    GuideNotVerified = 1,
    InvalidAmount = 2,
    SessionNotFound = 3,
    WrongStatus = 4,
    NotParticipant = 5,
    HoldNotOver = 6,
    HoldOver = 7,
    SameParty = 8,
}

#[contractevent]
pub struct CallOpened {
    #[topic]
    pub session_id: u64,
    pub user: Address,
    pub guide: Address,
    pub budget: i128,
}

#[contractevent]
pub struct CallClosed {
    #[topic]
    pub session_id: u64,
    pub seconds: u64,
    pub earned: i128,
    pub refunded: i128,
}

#[contractevent]
pub struct CallReported {
    #[topic]
    pub session_id: u64,
}

#[contractevent]
pub struct CallPaid {
    #[topic]
    pub session_id: u64,
    pub guide_amount: i128,
    pub fee: i128,
    pub user_refund: i128,
}

#[contract]
pub struct CallEscrow;

#[contractimpl]
impl CallEscrow {
    pub fn __constructor(env: Env, admin: Address, token: Address, hold_seconds: u64, fee_bps: u32) {
        if fee_bps as i128 > BPS_DENOMINATOR {
            panic_with_error!(&env, Error::InvalidAmount);
        }
        let storage = env.storage().instance();
        storage.set(&DataKey::Admin, &admin);
        storage.set(&DataKey::Token, &token);
        storage.set(&DataKey::HoldSeconds, &hold_seconds);
        storage.set(&DataKey::FeeBps, &fee_bps);
        storage.set(&DataKey::NextId, &1u64);
    }

    // ---- guide registry -------------------------------------------------

    pub fn verify_guide(env: Env, guide: Address) {
        admin(&env).require_auth();
        let key = DataKey::Guide(guide);
        env.storage().persistent().set(&key, &true);
        env.storage().persistent().extend_ttl(&key, TTL_THRESHOLD, TTL_EXTEND_TO);
    }

    pub fn revoke_guide(env: Env, guide: Address) {
        admin(&env).require_auth();
        env.storage().persistent().remove(&DataKey::Guide(guide));
    }

    pub fn is_verified(env: Env, guide: Address) -> bool {
        env.storage()
            .persistent()
            .get(&DataKey::Guide(guide))
            .unwrap_or(false)
    }

    // ---- call lifecycle -------------------------------------------------

    pub fn open(
        env: Env,
        user: Address,
        guide: Address,
        rate_per_minute: i128,
        budget: i128,
    ) -> Result<u64, Error> {
        user.require_auth();
        if user == guide {
            return Err(Error::SameParty);
        }
        if rate_per_minute <= 0 || budget <= 0 {
            return Err(Error::InvalidAmount);
        }
        if !Self::is_verified(env.clone(), guide.clone()) {
            return Err(Error::GuideNotVerified);
        }

        token_client(&env).transfer(&user, &env.current_contract_address(), &budget);

        let session_id: u64 = env.storage().instance().get(&DataKey::NextId).unwrap_or(1);
        env.storage().instance().set(&DataKey::NextId, &(session_id + 1));
        env.storage()
            .instance()
            .extend_ttl(TTL_THRESHOLD, TTL_EXTEND_TO);

        let session = Session {
            user: user.clone(),
            guide: guide.clone(),
            rate_per_minute,
            budget,
            started_at: env.ledger().timestamp(),
            ended_at: 0,
            earned: 0,
            status: Status::Open,
            recording_hash: None,
        };
        save_session(&env, session_id, &session);

        CallOpened {
            session_id,
            user,
            guide,
            budget,
        }
        .publish(&env);
        Ok(session_id)
    }

    /// Either participant ends the call. Returns the amount earned by the guide.
    pub fn close(
        env: Env,
        caller: Address,
        session_id: u64,
        recording_hash: Option<BytesN<32>>,
    ) -> Result<i128, Error> {
        caller.require_auth();
        let mut session = load_session(&env, session_id)?;
        if caller != session.user && caller != session.guide {
            return Err(Error::NotParticipant);
        }
        if session.status != Status::Open {
            return Err(Error::WrongStatus);
        }

        let now = env.ledger().timestamp();
        let seconds = now.saturating_sub(session.started_at);
        let earned = cost_for(session.rate_per_minute, seconds, session.budget);
        let refunded = session.budget - earned;
        if refunded > 0 {
            token_client(&env).transfer(&env.current_contract_address(), &session.user, &refunded);
        }

        session.ended_at = now;
        session.earned = earned;
        session.recording_hash = recording_hash;
        session.status = if earned == 0 {
            Status::Released
        } else {
            Status::Held
        };
        save_session(&env, session_id, &session);

        CallClosed {
            session_id,
            seconds,
            earned,
            refunded,
        }
        .publish(&env);
        Ok(earned)
    }

    /// The user is satisfied and releases the guide's earnings before the hold ends.
    pub fn confirm(env: Env, session_id: u64) -> Result<(), Error> {
        let mut session = load_session(&env, session_id)?;
        session.user.require_auth();
        if session.status != Status::Held {
            return Err(Error::WrongStatus);
        }
        pay_out(&env, session_id, &mut session, 0, Status::Released);
        Ok(())
    }

    /// The user disputes the call while the hold is still running.
    pub fn report(env: Env, session_id: u64) -> Result<(), Error> {
        let mut session = load_session(&env, session_id)?;
        session.user.require_auth();
        if session.status != Status::Held {
            return Err(Error::WrongStatus);
        }
        if env.ledger().timestamp() >= hold_ends_at(&env, &session) {
            return Err(Error::HoldOver);
        }
        session.status = Status::Disputed;
        save_session(&env, session_id, &session);
        CallReported { session_id }.publish(&env);
        Ok(())
    }

    /// After human review, the admin refunds part (or all) of the disputed earnings.
    pub fn resolve(env: Env, session_id: u64, user_refund: i128) -> Result<(), Error> {
        admin(&env).require_auth();
        let mut session = load_session(&env, session_id)?;
        if session.status != Status::Disputed {
            return Err(Error::WrongStatus);
        }
        if user_refund < 0 || user_refund > session.earned {
            return Err(Error::InvalidAmount);
        }
        pay_out(&env, session_id, &mut session, user_refund, Status::Resolved);
        Ok(())
    }

    /// The guide withdraws earnings once the hold is over. Returns the amount paid to the guide.
    pub fn claim(env: Env, session_id: u64) -> Result<i128, Error> {
        let mut session = load_session(&env, session_id)?;
        session.guide.require_auth();
        if session.status != Status::Held {
            return Err(Error::WrongStatus);
        }
        if env.ledger().timestamp() < hold_ends_at(&env, &session) {
            return Err(Error::HoldNotOver);
        }
        Ok(pay_out(&env, session_id, &mut session, 0, Status::Released))
    }

    // ---- views ----------------------------------------------------------

    pub fn get_session(env: Env, session_id: u64) -> Result<Session, Error> {
        load_session(&env, session_id)
    }

    pub fn hold_seconds(env: Env) -> u64 {
        env.storage().instance().get(&DataKey::HoldSeconds).unwrap_or(0)
    }

    pub fn fee_bps(env: Env) -> u32 {
        env.storage().instance().get(&DataKey::FeeBps).unwrap_or(0)
    }

    pub fn token(env: Env) -> Address {
        env.storage().instance().get(&DataKey::Token).unwrap()
    }
}

// ---- helpers ---------------------------------------------------------------

fn admin(env: &Env) -> Address {
    env.storage().instance().get(&DataKey::Admin).unwrap()
}

fn token_client(env: &Env) -> token::Client<'_> {
    let token: Address = env.storage().instance().get(&DataKey::Token).unwrap();
    token::Client::new(env, &token)
}

fn load_session(env: &Env, session_id: u64) -> Result<Session, Error> {
    env.storage()
        .persistent()
        .get(&DataKey::Session(session_id))
        .ok_or(Error::SessionNotFound)
}

fn save_session(env: &Env, session_id: u64, session: &Session) {
    let key = DataKey::Session(session_id);
    env.storage().persistent().set(&key, session);
    env.storage()
        .persistent()
        .extend_ttl(&key, TTL_THRESHOLD, TTL_EXTEND_TO);
}

fn hold_ends_at(env: &Env, session: &Session) -> u64 {
    let hold: u64 = env.storage().instance().get(&DataKey::HoldSeconds).unwrap_or(0);
    session.ended_at.saturating_add(hold)
}

/// Per-second billing, rounded up to the smallest token unit, capped at the budget.
fn cost_for(rate_per_minute: i128, seconds: u64, budget: i128) -> i128 {
    let raw = rate_per_minute.saturating_mul(seconds as i128);
    let cost = (raw + 59) / 60;
    if cost > budget {
        budget
    } else {
        cost
    }
}

/// Splits held earnings between the user (refund), the platform (fee) and the guide.
fn pay_out(
    env: &Env,
    session_id: u64,
    session: &mut Session,
    user_refund: i128,
    final_status: Status,
) -> i128 {
    let remaining = session.earned - user_refund;
    let fee_bps: u32 = env.storage().instance().get(&DataKey::FeeBps).unwrap_or(0);
    let fee = remaining * fee_bps as i128 / BPS_DENOMINATOR;
    let guide_amount = remaining - fee;

    let token = token_client(env);
    let escrow = env.current_contract_address();
    if user_refund > 0 {
        token.transfer(&escrow, &session.user, &user_refund);
    }
    if fee > 0 {
        token.transfer(&escrow, &admin(env), &fee);
    }
    if guide_amount > 0 {
        token.transfer(&escrow, &session.guide, &guide_amount);
    }

    session.status = final_status;
    save_session(env, session_id, session);
    CallPaid {
        session_id,
        guide_amount,
        fee,
        user_refund,
    }
    .publish(env);
    guide_amount
}

#[cfg(test)]
mod test;
