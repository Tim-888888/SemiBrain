# Third-party notices

The parser subset in `vendor/weknora-docreader` is copied from Tencent/WeKnora,
commit `c40a9dd1940f85d85a59787bd532eb0f290cb176`. Original parser sources are unchanged and retain the original
license in that directory. `provenance.json` lists SHA-256 hashes and upstream paths.
Only the selected parser dependency closure is packaged; no WeKnora service is deployed.
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
