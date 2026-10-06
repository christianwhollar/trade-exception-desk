"""Opt-in local EVM test; never sends a transaction to a public network."""

import os
from pathlib import Path
import pytest


@pytest.mark.skipif(
    os.getenv("RUN_CHAIN_TESTS") != "1",
    reason="Install .[chain], solc 0.8.30 and set RUN_CHAIN_TESTS=1",
)
def test_anchor_ownership_sequence_and_digest():
    import solcx
    from eth_tester.exceptions import TransactionFailed
    from web3 import Web3, EthereumTesterProvider

    source = (Path(__file__).resolve().parents[1] / "contracts/AuditAnchor.sol").read_text()
    contract = solcx.compile_source(source, output_values=["abi", "bin"], solc_version="0.8.30")[
        "<stdin>:AuditAnchor"
    ]
    web3 = Web3(EthereumTesterProvider())
    owner, stranger = web3.eth.accounts[:2]
    factory = web3.eth.contract(abi=contract["abi"], bytecode=contract["bin"])
    receipt = web3.eth.wait_for_transaction_receipt(factory.constructor().transact({"from": owner}))
    anchor = web3.eth.contract(address=receipt.contractAddress, abi=contract["abi"])
    namespace, digest = Web3.keccak(text="synthetic-alpha"), Web3.keccak(text="audit-head")
    anchor.functions.anchor(namespace, 1, digest).transact({"from": owner})
    assert anchor.functions.checkpoints(namespace).call() == [1, digest]
    with pytest.raises(TransactionFailed):
        anchor.functions.anchor(namespace, 2, digest).transact({"from": stranger})
    with pytest.raises(TransactionFailed):
        anchor.functions.anchor(namespace, 1, digest).transact({"from": owner})
    with pytest.raises(TransactionFailed):
        anchor.functions.anchor(namespace, 2, bytes(32)).transact({"from": owner})
