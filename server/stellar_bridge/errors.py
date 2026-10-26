import re

# Mirrors `Error` in contracts/call_escrow/src/lib.rs.
ESCROW_ERRORS = {
    1: ("GuideNotVerified", "This guide is not verified yet."),
    2: ("InvalidAmount", "The amount is not valid."),
    3: ("SessionNotFound", "That call was not found on-chain."),
    4: ("WrongStatus", "That action is not possible at this stage of the call."),
    5: ("NotParticipant", "Only the caller or the guide can do that."),
    6: ("HoldNotOver", "The hold period is not over yet."),
    7: ("HoldOver", "The time to report this call has passed."),
    8: ("SameParty", "You cannot call yourself."),
}

_CONTRACT_ERROR = re.compile(r"Error\(Contract, #(\d+)\)")


class StellarBridgeError(Exception):
    """Network, submission or configuration problem."""


class ContractError(StellarBridgeError):
    """The escrow contract rejected the call with a typed error."""

    def __init__(self, code: int | None, name: str, message: str, raw: str = ""):
        super().__init__(message)
        self.code = code
        self.name = name
        self.raw = raw

    @classmethod
    def from_text(cls, text: str) -> "ContractError":
        match = _CONTRACT_ERROR.search(text or "")
        if match:
            code = int(match.group(1))
            name, message = ESCROW_ERRORS.get(code, (f"Error{code}", f"Contract error {code}."))
            return cls(code, name, message, raw=text)
        return cls(None, "Unknown", "The transaction was rejected.", raw=text)
