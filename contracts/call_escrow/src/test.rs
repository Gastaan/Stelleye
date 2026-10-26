#![cfg(test)]
use super::*;
use soroban_sdk::{
    testutils::{Address as _, Ledger},
    token::{Client as TokenClient, StellarAssetClient},
    Address, BytesN, Env,
};

const HOLD: u64 = 86_400;
const FEE_BPS: u32 = 500; // 5%
const RATE: i128 = 20; // token units per minute
const BUDGET: i128 = 300;

struct Setup<'a> {
    env: Env,
    escrow: CallEscrowClient<'a>,
    token: TokenClient<'a>,
    admin: Address,
    user: Address,
    guide: Address,
}

fn setup<'a>() -> Setup<'a> {
    let env = Env::default();
    env.mock_all_auths();
    env.ledger().with_mut(|l| l.timestamp = 1_000);

    let admin = Address::generate(&env);
    let user = Address::generate(&env);
    let guide = Address::generate(&env);

    let sac = env.register_stellar_asset_contract_v2(admin.clone());
    StellarAssetClient::new(&env, &sac.address()).mint(&user, &1_000);
    let token = TokenClient::new(&env, &sac.address());

    let escrow_id = env.register(CallEscrow, (admin.clone(), sac.address(), HOLD, FEE_BPS));
    let escrow = CallEscrowClient::new(&env, &escrow_id);
    escrow.verify_guide(&guide);

    Setup { env, escrow, token, admin, user, guide }
}

fn advance(env: &Env, seconds: u64) {
    env.ledger().with_mut(|l| l.timestamp += seconds);
}

#[test]
fn bills_by_ledger_time_and_refunds_the_rest() {
    let s = setup();
    let id = s.escrow.open(&s.user, &s.guide, &RATE, &BUDGET);
    assert_eq!(s.token.balance(&s.user), 700);

    advance(&s.env, 150); // 2.5 minutes at 20/min = 50
    let earned = s.escrow.close(&s.user, &id, &None);
    assert_eq!(earned, 50);
    assert_eq!(s.token.balance(&s.user), 950);

    let session = s.escrow.get_session(&id);
    assert_eq!(session.status, Status::Held);
    assert_eq!(session.ended_at - session.started_at, 150);
}

#[test]
fn guide_can_close_and_cost_is_capped_at_budget() {
    let s = setup();
    let id = s.escrow.open(&s.user, &s.guide, &RATE, &BUDGET);
    advance(&s.env, 3_600); // an hour would cost 1200, budget is 300
    assert_eq!(s.escrow.close(&s.guide, &id, &None), BUDGET);
    assert_eq!(s.token.balance(&s.user), 700);
}

#[test]
fn rounds_partial_units_up() {
    let s = setup();
    let id = s.escrow.open(&s.user, &s.guide, &RATE, &BUDGET);
    advance(&s.env, 1); // 20/60 of a unit rounds up to 1
    assert_eq!(s.escrow.close(&s.user, &id, &None), 1);
}

#[test]
fn guide_claims_only_after_hold() {
    let s = setup();
    let id = s.escrow.open(&s.user, &s.guide, &RATE, &BUDGET);
    advance(&s.env, 150);
    s.escrow.close(&s.user, &id, &None);

    assert_eq!(s.escrow.try_claim(&id), Err(Ok(Error::HoldNotOver)));

    advance(&s.env, HOLD);
    assert_eq!(s.escrow.claim(&id), 48); // 50 minus 5% fee (2, rounded down)
    assert_eq!(s.token.balance(&s.guide), 48);
    assert_eq!(s.token.balance(&s.admin), 2);
    assert_eq!(s.escrow.get_session(&id).status, Status::Released);
}

#[test]
fn user_confirm_releases_early() {
    let s = setup();
    let id = s.escrow.open(&s.user, &s.guide, &RATE, &BUDGET);
    advance(&s.env, 150);
    s.escrow.close(&s.guide, &id, &None);
    s.escrow.confirm(&id);
    assert_eq!(s.token.balance(&s.guide), 48);
    assert_eq!(s.escrow.try_claim(&id), Err(Ok(Error::WrongStatus)));
}

#[test]
fn report_then_admin_resolves_with_partial_refund() {
    let s = setup();
    let id = s.escrow.open(&s.user, &s.guide, &RATE, &BUDGET);
    advance(&s.env, 150);
    let hash = BytesN::from_array(&s.env, &[7u8; 32]);
    s.escrow.close(&s.user, &id, &Some(hash.clone()));
    assert_eq!(s.escrow.get_session(&id).recording_hash, Some(hash));

    s.escrow.report(&id);
    assert_eq!(s.escrow.get_session(&id).status, Status::Disputed);
    advance(&s.env, HOLD);
    assert_eq!(s.escrow.try_claim(&id), Err(Ok(Error::WrongStatus)));

    s.escrow.resolve(&id, &20); // user gets 20 back, 30 remain: fee 1, guide 29
    assert_eq!(s.token.balance(&s.user), 970);
    assert_eq!(s.token.balance(&s.guide), 29);
    assert_eq!(s.token.balance(&s.admin), 1);
    assert_eq!(s.escrow.get_session(&id).status, Status::Resolved);
}

#[test]
fn report_rejected_after_hold() {
    let s = setup();
    let id = s.escrow.open(&s.user, &s.guide, &RATE, &BUDGET);
    advance(&s.env, 150);
    s.escrow.close(&s.user, &id, &None);
    advance(&s.env, HOLD);
    assert_eq!(s.escrow.try_report(&id), Err(Ok(Error::HoldOver)));
}

#[test]
fn rejects_unverified_guide_and_bad_input() {
    let s = setup();
    let stranger = Address::generate(&s.env);
    assert_eq!(
        s.escrow.try_open(&s.user, &stranger, &RATE, &BUDGET),
        Err(Ok(Error::GuideNotVerified))
    );
    assert_eq!(
        s.escrow.try_open(&s.user, &s.guide, &0, &BUDGET),
        Err(Ok(Error::InvalidAmount))
    );
    s.escrow.verify_guide(&s.user);
    assert_eq!(
        s.escrow.try_open(&s.user, &s.user, &RATE, &BUDGET),
        Err(Ok(Error::SameParty))
    );
}

#[test]
fn only_participants_close_and_only_once() {
    let s = setup();
    let id = s.escrow.open(&s.user, &s.guide, &RATE, &BUDGET);
    let stranger = Address::generate(&s.env);
    assert_eq!(
        s.escrow.try_close(&stranger, &id, &None),
        Err(Ok(Error::NotParticipant))
    );
    advance(&s.env, 60);
    s.escrow.close(&s.user, &id, &None);
    assert_eq!(
        s.escrow.try_close(&s.guide, &id, &None),
        Err(Ok(Error::WrongStatus))
    );
}

#[test]
fn instant_hangup_costs_nothing() {
    let s = setup();
    let id = s.escrow.open(&s.user, &s.guide, &RATE, &BUDGET);
    assert_eq!(s.escrow.close(&s.user, &id, &None), 0);
    assert_eq!(s.token.balance(&s.user), 1_000);
    assert_eq!(s.escrow.get_session(&id).status, Status::Released);
}

#[test]
fn revoked_guide_cannot_take_new_calls() {
    let s = setup();
    s.escrow.revoke_guide(&s.guide);
    assert!(!s.escrow.is_verified(&s.guide));
    assert_eq!(
        s.escrow.try_open(&s.user, &s.guide, &RATE, &BUDGET),
        Err(Ok(Error::GuideNotVerified))
    );
}
