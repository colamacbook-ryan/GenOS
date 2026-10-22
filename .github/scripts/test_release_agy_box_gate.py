"""Cổng phát hành phải kiểm cô lập agy-box (v0.1.63 H3): release.yml job pin-compose chạy check_agy_box_isolation.py --release
trên compose.release.yaml SAU khi ghim digest và TRƯỚC khi upload artifact chở vào genh/Release."""

import unittest
from pathlib import Path

import yaml  # type: ignore[import-untyped]  # PyYAML không kèm stub kiểu

REPO = Path(__file__).resolve().parents[2]
WF = REPO / ".github" / "workflows"


def _steps(workflow: str, job: str) -> list[dict]:
    data = yaml.safe_load((WF / workflow).read_text(encoding="utf-8"))
    return data["jobs"][job]["steps"]


class ReleaseAgyBoxGate(unittest.TestCase):
    def test_pin_compose_checks_agy_box_isolation_between_pin_and_upload(self) -> None:
        steps = _steps("release.yml", "pin-compose")
        pin = next(i for i, s in enumerate(steps) if "pin-compose-images.sh" in str(s.get("run", "")))
        upload = next(i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith("actions/upload-artifact"))
        gate = [i for i, s in enumerate(steps) if "check_agy_box_isolation.py" in str(s.get("run", ""))]
        self.assertEqual(len(gate), 1, "phải có đúng một bước kiểm cô lập agy-box trong job pin-compose")
        self.assertTrue(pin < gate[0] < upload, "bước kiểm phải nằm giữa bước ghim digest và bước upload artifact")
        run = steps[gate[0]]["run"]
        self.assertIn("deploy/compose.release.yaml", run)
        self.assertIn("--release", run)
        self.assertNotIn("continue-on-error", steps[gate[0]], "bước kiểm không được phép bỏ qua lỗi")


    def _idx(self, steps: list[dict], pred) -> list[int]:
        return [i for i, s in enumerate(steps) if pred(s)]

    def test_e2e_install_checks_box_after_status(self) -> None:
        steps = _steps("e2e-install.yml", "e2e-install")
        status = self._idx(steps, lambda s: str(s.get("name", "")).startswith("genh status (kiểm mọi dịch vụ healthy)"))
        gate = self._idx(steps, lambda s: "e2e_agy_box.sh" in str(s.get("run", "")))
        self.assertEqual(len(status), 1)
        self.assertEqual(len(gate), 1, "e2e-install phải chạy e2e_agy_box.sh đúng một lần")
        self.assertGreater(gate[0], status[0], "kiểm box phải sau khi mọi dịch vụ đã healthy")
        self.assertNotIn("continue-on-error", steps[gate[0]])

    def test_e2e_upgrade_records_key_before_and_checks_box_after_update(self) -> None:
        steps = _steps("e2e-install.yml", "e2e-upgrade")
        before = self._idx(steps, lambda s: "gh_agy_key" in str(s.get("run", "")) and s.get("id") == "agykey")
        update = self._idx(steps, lambda s: str(s.get("name", "")).startswith("genh update --yes --no-self-update"))
        gate = self._idx(steps, lambda s: "e2e_agy_box.sh" in str(s.get("run", "")))
        uninstall = self._idx(steps, lambda s: str(s.get("name", "")).startswith("genh uninstall"))
        self.assertEqual((len(before), len(update), len(gate), len(uninstall)), (1, 1, 1, 1))
        self.assertLess(before[0], update[0], "phải ghi lại gh_agy_key TRƯỚC genh update")
        self.assertTrue(update[0] < gate[0] < uninstall[0], "kiểm box phải sau genh update và trước dọn dẹp")
        env = steps[gate[0]].get("env", {})
        self.assertEqual(env.get("MODE"), "release")
        self.assertIn("agykey", str(env), "bước kiểm phải nhận trạng thái gh_agy_key trước nâng cấp")
        self.assertNotIn("continue-on-error", steps[gate[0]])

    def test_script_changes_trigger_e2e(self) -> None:
        on = yaml.safe_load((WF / "e2e-install.yml").read_text(encoding="utf-8"))
        trig = on.get("on") or on.get(True)  # PyYAML đọc khoá `on` thành True
        for ev in ("pull_request", "push"):
            self.assertIn(".github/scripts/e2e_agy_box.sh", trig[ev]["paths"], ev)


if __name__ == "__main__":
    unittest.main()
