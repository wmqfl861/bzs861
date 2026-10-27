# Temporary original-commit transport — DO NOT MERGE

This branch transfers six reviewed original commits to the same authorized repository. Only those original commits belong on the delivery branch; this transport commit and its files must never be merged into main or the Claude review branches.

Incremental bundle: 31818 bytes; SHA256 `21f43a1d63c33569813d57e50650f5e7e4ca9e01c75c525818e8dce9029ecb7a`.
Base: `1d8e80e5f1bd1b5f9ad8d1e5fc211c8cd403ca31`.
Target: `d29223551041f5d0f39fa4ca4c6ce936af5a89d9`.
Product tree: `d97ff8f5bdd10fa10b085573e508f3d1c2919f24`.

Each chunk's returned Git blob was matched to the local byte hash. The import also verifies the complete bundle and the exact ordered six commits. It runs no imported product code and performs no forced ref update. No runtime databases, credentials, raw logs or dependency wheels are included.

Inherited CI/snapshot workflows are omitted ONLY on this transport branch to avoid testing transport files; the original product tree retains its full strengthened CI. The actual delivery branch must be published through the connector and checked independently. A denied write must stop, not trigger a permissions workaround.
