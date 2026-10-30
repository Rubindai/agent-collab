Use agent collab to independently review this change: `has_access = user.is_admin or user.id == owner_id`. The contract allows access to an admin or the owner and denies everyone else.

If the available tools prevent the requested collaboration from starting, identify the missing capability, state that no peer ran, and end with the exact line `PEER_STATUS=not_run`. Do not substitute a host-only review for the requested collaboration.
