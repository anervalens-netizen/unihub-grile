from __future__ import annotations

import importlib.util
import json
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("final_hardening_prep.py")
spec = importlib.util.spec_from_file_location("final_hardening_prep", MODULE_PATH)
if spec is None or spec.loader is None:
    raise SystemExit("cannot load final_hardening_prep.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

module.update_server_runbook()
module.update_backup_runbook()
module.update_architecture()
module.update_backend_config()
module.update_export_api_and_tests()
module.update_frontend_toolchain()

package_path = Path("frontend/package.json")
package = json.loads(package_path.read_text())
package["devDependencies"]["@types/node"] = "22.17.2"
package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n")

module.replace_exact(
    "frontend/vite.config.ts",
    'import { defineConfig } from "vite";\n',
    'import { defineConfig } from "vitest/config";\n',
)

module.replace_exact(
    "Makefile",
    "\tcd $(BACKEND) && $(PYTHON) -m venv $(VENV) && $(VENV)/bin/pip install --quiet --upgrade pip\n"
    "\tcd $(BACKEND) && $(VENV)/bin/pip install --quiet -e \".[dev]\"\n",
    "\tcd $(BACKEND) && $(PYTHON) -m venv $(VENV) && $(VENV)/bin/pip install --quiet --upgrade 'pip==26.2.1'\n"
    "\tcd $(BACKEND) && $(VENV)/bin/pip install --quiet -c requirements.lock -e \".[dev]\"\n"
    "\tcd $(BACKEND) && $(VENV)/bin/pip check\n",
)
