# Reporting security issues

For a security issue that could expose credentials, modify unintended files, bypass source/hash checks, or execute unexpected native code, contact the maintainer privately at **hu_bo_cheng@qq.com**. Include the toolkit version/commit, relevant command and a small synthetic reproducer when possible. Do not post credentials, proprietary game binaries, launcher databases, local debug dumps or private Mod packages in a public issue.

Use public issues for ordinary defects with sanitized input and diagnostics. Binary updates that are deliberately refused by an unknown-version guard are compatibility requests; do not bypass the hash check to produce a report.

The project is a development preview. CI covers synthetic and isolated cases; it does not certify every input, native DLL behavior or gameplay. Reported support is limited to the reviewed binary profiles and operations in `native-status.available_operations`.
