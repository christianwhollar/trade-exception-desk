// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;

/// @notice Optional audit checkpoint registry. No trade execution, funds, or personal data.
contract AuditAnchor {
    address public immutable owner;
    struct Checkpoint { uint256 sequence; bytes32 digest; }
    mapping(bytes32 => Checkpoint) public checkpoints;
    event Anchored(bytes32 indexed namespace, uint256 sequence, bytes32 digest);

    constructor() { owner = msg.sender; }

    function anchor(bytes32 namespace, uint256 sequence, bytes32 digest) external {
        require(msg.sender == owner, "owner required");
        require(sequence > checkpoints[namespace].sequence, "sequence must increase");
        require(digest != bytes32(0), "empty digest");
        checkpoints[namespace] = Checkpoint(sequence, digest);
        emit Anchored(namespace, sequence, digest);
    }
}
