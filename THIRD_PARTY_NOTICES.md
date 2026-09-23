# Third-party notices

The parser subset in `vendor/weknora-docreader` is copied from Tencent/WeKnora,
commit `c40a9dd1940f85d85a59787bd532eb0f290cb176`. Original parser sources are unchanged and retain the original
license in that directory. `provenance.json` lists SHA-256 hashes and upstream paths.
Only the selected parser dependency closure is packaged; no WeKnora service is deployed.

The Python Markdown chunker and document-image mapper in
`services/business-service/app/semibrain_business/{chunking,document_images}.py`
adapt design and algorithmic ideas from Tencent/WeKnora commit
`1fa915d712288902da7841dc8ff355a56e37a013`: heading breadcrumbs stored separately
from source offsets, protected syntax spans, bounded structural fallback, and
original-resource-to-stored-image mappings. References are
`internal/infrastructure/chunker/{heading_splitter,splitter,strategy}.go` and
`internal/infrastructure/docparser/{image_resolver,markdown_image_scanner}.go`.
This is a Python adaptation with independent tests, not a deployment of the Go
chunking service. The upstream license is preserved in
`vendor/weknora-docreader/LICENSE`; upstream copyright and dependency notices
continue to apply. The parser subset above remains pinned to its original commit.

Inline image presentation also follows the general Markdown/path-resolution
design inspected in DeepSeek Harness commit
`46a7f68b0922371ce7144b668b90e377d8e799f4`. No DSH source was copied into the Vue UI.
Dependency metadata is recorded in `infra/images/python-licenses.json` and
`infra/images/node-licenses.json`. `UNKNOWN` in generated metadata is not a license:
the SemiBrain workspace packages do not declare a project-wide license, the vendored
WeKnora subset uses its included upstream LICENSE, and the two MongoDB integration
packages require the supplemental source notice below.

The installed `langchain-mongodb` wheel includes an MIT license under its
`dist-info/licenses/LICENSE`. `langgraph-checkpoint-mongodb` 0.5.0 does not include
license metadata or a license file in its wheel. The upstream integration repository
publishes an [MIT license](https://github.com/langchain-ai/langchain-mongodb/blob/main/LICENSE).
Preserve that notice with any distribution of the integration dependencies; the
generated inventory intentionally retains the wheel's missing-metadata finding.

Container components keep their own licenses, including the
[MongoDB community server license](https://github.com/mongodb/mongo/blob/master/LICENSE-Community.txt).
The foundation deployment does not relicense or redistribute base images in this Git repository.

The Markdown renderer uses the MIT-licensed [KaTeX](https://github.com/KaTeX/KaTeX)
and [markdown-it-texmath](https://github.com/goessner/markdown-it-texmath) packages.
Their code is installed from the locked packages; their bundled notices and KaTeX
font licenses must accompany redistributed application builds.
