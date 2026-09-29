"""Fixed Office conversion inside the existing network-disabled Docker boundary."""

import base64
import io
from uuid import uuid4

from semibrain_business.sandbox import provider


def validate_legacy(content):
    import olefile
    if len(content) > 12 * 1024**2:
        raise ValueError("LEGACY_OFFICE_SIZE_LIMIT")
    if not olefile.isOleFile(io.BytesIO(content)):
        raise ValueError("INVALID_LEGACY_OFFICE_SIGNATURE")
    with olefile.OleFileIO(io.BytesIO(content)) as archive:
        paths = [tuple(p.casefold() for p in parts) for parts in archive.listdir()]
        if any(any(p in {"vba", "_vba_project_cur", "macros"} for p in parts) for parts in paths):
            raise ValueError("OFFICE_MACROS_UNSUPPORTED")
        if any("encryptedpackage" in parts for parts in paths):
            raise ValueError("ENCRYPTED_OFFICE_UNSUPPORTED")


def convert(content, extension, progress):
    validate_legacy(content)
    target = {"doc": "docx", "ppt": "pptx"}[extension]
    identity = uuid4().hex + uuid4().hex
    progress({"sandbox_identity": identity})
    # Input names and filter are selected from constants, never document content.
    code = '''from pathlib import Path
import subprocess
profile=Path('/tmp/office-profile');profile.mkdir()
(profile/'user').mkdir()
(profile/'user/registrymodifications.xcu').write_text('<oor:items xmlns:oor="http://openoffice.org/2001/registry"><item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item></oor:items>')
subprocess.run(['/usr/bin/soffice','-env:UserInstallation=file:///tmp/office-profile','--headless','--nologo','--nodefault','--norestore','--convert-to',TARGET,'--outdir','/workspace',SOURCE],check=True,timeout=24,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
assert Path('/workspace/input.'+TARGET).is_file(), 'OFFICE_CONVERSION_NO_OUTPUT'
'''.replace("TARGET", repr(target)).replace("SOURCE", repr("/workspace/input." + extension))
    sandbox = provider()
    try:
        result = sandbox.execute({"identity": identity, "code": code, "seconds": 28,
                                  "files": {"input." + extension: base64.b64encode(content).decode()},
                                  "exports": ["input." + target]})
        if result.get("exit_code") != 0 or len(result.get("files", [])) != 1:
            raise ValueError("OFFICE_CONVERSION_FAILED")
        return base64.b64decode(result["files"][0]["base64"], validate=True), target
    finally:
        sandbox.destroy(identity)
