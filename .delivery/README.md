# Temporary commit transport

This auxiliary branch transports a tested local Git bundle into the same authorized repository because the isolated build container has no outbound DNS. It is not a development or review branch and must not be merged. Only the contained commit may be published to the delivery branch. Main and both Claude review branches are untouched. No runtime data, raw test logs, dependency wheels or credentials are included.

P1-B: commit17783391143478209105a300d8eb174feab7df85, tree55025a502fb02f048a40503de9d969982b9e6161, parentd62473d4d5265e43f350909e27e32ddb1c01f95a. All five transferred Git blobs matched their locally computed object IDs. The workflow verifies the complete bundle before non-force publication. A denied write is a failure, not permission to bypass controls.
