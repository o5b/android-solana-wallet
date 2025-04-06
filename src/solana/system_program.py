"""Library to interface with the system program."""
from typing import NamedTuple, List
# from dataclasses import dataclass

from .publickey import PublicKey
from ._layouts.system_instructions import SYSTEM_INSTRUCTIONS_LAYOUT, InstructionType
from .transaction import AccountMeta, TransactionInstruction

SYS_PROGRAM_ID: PublicKey = PublicKey("11111111111111111111111111111111")
"""Public key that identifies the System program."""


class TransferParams(NamedTuple):
    """Transfer system transaction params."""

    from_pubkey: PublicKey
    """"""
    to_pubkey: PublicKey
    """"""
    lamports: int


def transfer(params: TransferParams) -> TransactionInstruction:
    """Generate an instruction that transfers lamports from one account to another.

    Args:
        params: The transfer params.

    Example:

        >>> from solana.publickey import PublicKey
        >>> sender, receiver = PublicKey(1), PublicKey(2)
        >>> instruction = transfer(
        ...     TransferParams(from_pubkey=sender, to_pubkey=receiver, lamports=1000)
        ... )
        >>> type(instruction)
        <class 'solana.transaction.TransactionInstruction'>

    Returns:
        The transfer instruction.

    """
    data = SYSTEM_INSTRUCTIONS_LAYOUT.build(
        dict(instruction_type=InstructionType.TRANSFER, args=dict(lamports=params.lamports))
    )

    return TransactionInstruction(
        keys=[
            AccountMeta(pubkey=params.from_pubkey, is_signer=True, is_writable=True),
            AccountMeta(pubkey=params.to_pubkey, is_signer=False, is_writable=True),
        ],
        program_id=SYS_PROGRAM_ID,
        data=data,
    )