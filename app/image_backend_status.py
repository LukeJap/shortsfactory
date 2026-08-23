from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import sys
from pathlib import Path
from typing import Any

import requests


DEFAULT_API = os.getenv(
    "SHORTSFACTORY_IMAGE_API",
    "http://127.0.0.1:7860",
).rstrip("/")

def _default_forge_launch_path() -> str:
    """
    Best-guess default install location for Forge's launcher script, in
    whatever form is native to this OS. Always overridable via the
    SHORTSFACTORY_FORGE_LAUNCH env var regardless of platform.
    """

    if sys.platform.startswith("win"):
        return r"C:\AI\Forge\run.bat"

    return str(Path.home() / "AI" / "Forge" / "webui.sh")


DEFAULT_FORGE_LAUNCH = Path(
    os.getenv(
        "SHORTSFACTORY_FORGE_LAUNCH",
        _default_forge_launch_path(),
    )
)

ROOT = Path(__file__).resolve().parent.parent
LAUNCH_LOCK_PATH = ROOT / "output" / "image_ai_launch.lock"


class WebUIImageProvider:
    """
    Narrow adapter for Forge / Automatic1111-compatible image APIs.
    GUI code should consume the simple status payload rather than spreading
    API endpoint details through the editor.
    """

    def __init__(
        self,
        api: str = DEFAULT_API,
    ):
        self.api = api.rstrip("/")

    def _get_json(
        self,
        endpoint: str,
        timeout: float = 4.0,
    ) -> Any:
        response = requests.get(
            self.api + endpoint,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    def _post_json(
        self,
        endpoint: str,
        payload: dict[str, Any],
        timeout: float = 30.0,
    ) -> Any:
        response = requests.post(
            self.api + endpoint,
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        if response.content:
            return response.json()
        return {}

    def get_options(self) -> dict[str, Any]:
        data = self._get_json(
            "/sdapi/v1/options",
        )
        return data if isinstance(
            data,
            dict,
        ) else {}

    def get_models(self) -> list[dict[str, str]]:
        data = self._get_json(
            "/sdapi/v1/sd-models",
        )
        if not isinstance(
            data,
            list,
        ):
            return []

        models: list[dict[str, str]] = []
        for item in data:
            if not isinstance(
                item,
                dict,
            ):
                continue

            title = str(
                item.get(
                    "title",
                    "",
                )
                or ""
            ).strip()

            model_name = str(
                item.get(
                    "model_name",
                    "",
                )
                or ""
            ).strip()

            if not title and model_name:
                title = model_name

            if not model_name and title:
                model_name = Path(
                    title.split(
                        "[",
                        1,
                    )[0].strip()
                ).stem

            if title:
                models.append(
                    {
                        "title": title,
                        "name": model_name or title,
                    }
                )

        return models

    def set_model(
        self,
        model_title: str,
    ) -> None:
        self._post_json(
            "/sdapi/v1/options",
            {
                "sd_model_checkpoint": model_title,
            },
            timeout=120.0,
        )

    def status(
        self,
    ) -> dict[str, Any]:
        try:
            options = self.get_options()
            models = self.get_models()
        except requests.RequestException as exc:
            return {
                "state": "offline",
                "message": "Could not connect to Image AI.",
                "models": [],
                "current_model": "",
                "current_model_title": "",
                "error": str(exc),
            }
        except Exception as exc:
            return {
                "state": "error",
                "message": "Image AI returned an unexpected response.",
                "models": [],
                "current_model": "",
                "current_model_title": "",
                "error": str(exc),
            }

        current_title = str(
            options.get(
                "sd_model_checkpoint",
                "",
            )
            or ""
        ).strip()

        if not models:
            return {
                "state": "connected_no_model",
                "message": "No image model installed.",
                "models": [],
                "current_model": "",
                "current_model_title": current_title,
            }

        current_name = ""
        for model in models:
            if model.get(
                "title"
            ) == current_title:
                current_name = str(
                    model.get(
                        "name",
                        "",
                    )
                )
                break

        if not current_name:
            current_name = Path(
                current_title.split(
                    "[",
                    1,
                )[0].strip()
            ).stem

        return {
            "state": "ready",
            "message": "Image AI ready.",
            "models": models,
            "current_model": current_name,
            "current_model_title": current_title,
        }


def launch_forge(
    launch_path: Path = DEFAULT_FORGE_LAUNCH,
) -> tuple[bool, str]:

    if not launch_path.exists():
        return (
            False,
            f"Forge launcher not found: {launch_path}",
        )

    try:
        if sys.platform.startswith("win"):
            creationflags = getattr(
                subprocess,
                "CREATE_NEW_CONSOLE",
                0,
            )
            subprocess.Popen(
                [
                    "cmd.exe",
                    "/c",
                    str(
                        launch_path
                    ),
                ],
                cwd=str(
                    launch_path.parent
                ),
                creationflags=creationflags,
            )
        else:
            subprocess.Popen(
                [
                    "/bin/bash",
                    str(
                        launch_path
                    ),
                ],
                cwd=str(
                    launch_path.parent
                ),
                start_new_session=True,
            )
    except OSError as exc:
        return (
            False,
            f"Could not start Forge: {exc}",
        )

    return (
        True,
        "Forge launch requested.",
    )


def launch_lock_is_fresh(
    max_age_seconds: float = 300.0,
) -> bool:

    try:
        stamp = float(
            LAUNCH_LOCK_PATH.read_text(
                encoding="utf-8",
            ).strip()
        )
    except (
        OSError,
        ValueError,
    ):
        return False

    return (
        time.time()
        - stamp
    ) < max_age_seconds


def write_launch_lock() -> None:

    try:
        LAUNCH_LOCK_PATH.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        LAUNCH_LOCK_PATH.write_text(
            str(
                time.time()
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def status_with_optional_launch(
    provider: WebUIImageProvider,
    *,
    autolaunch: bool = False,
    wait_seconds: float = 180.0,
    poll_seconds: float = 3.0,
) -> dict[str, Any]:

    status = provider.status()
    if status.get(
        "state"
    ) in {
        "ready",
        "connected_no_model",
    }:
        status["started_by_shortsfactory"] = False
        return status

    if not autolaunch:
        status["started_by_shortsfactory"] = False
        return status

    if launch_lock_is_fresh():
        launched = True
        message = "Forge launch already requested; waiting for backend."
    else:
        launched, message = launch_forge()
        if not launched:
            return {
                **status,
                "state": "error",
                "message": message,
                "started_by_shortsfactory": False,
            }
        write_launch_lock()

    deadline = time.monotonic() + max(
        0.0,
        wait_seconds,
    )
    last_status = {
        **status,
        "state": "starting",
        "message": message,
        "started_by_shortsfactory": True,
    }

    if wait_seconds <= 0.0:
        return last_status

    while time.monotonic() < deadline:
        time.sleep(
            max(
                0.25,
                poll_seconds,
            )
        )
        last_status = provider.status()
        last_status["started_by_shortsfactory"] = True
        if last_status.get(
            "state"
        ) in {
            "ready",
            "connected_no_model",
        }:
            return last_status
        last_status["state"] = "waiting"
        last_status["message"] = "Waiting for Image AI backend..."

    return {
        **last_status,
        "state": "offline",
        "message": "Timed out waiting for Image AI backend.",
        "started_by_shortsfactory": True,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check ShortsFactory Image AI backend status.",
    )
    parser.add_argument(
        "--api",
        default=DEFAULT_API,
    )
    parser.add_argument(
        "--set-model",
        default="",
    )
    parser.add_argument(
        "--autolaunch",
        action="store_true",
        help="Start Stable Diffusion Forge if the API is offline.",
    )
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=180.0,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    provider = WebUIImageProvider(
        args.api,
    )

    before = status_with_optional_launch(
        provider,
        autolaunch=args.autolaunch,
        wait_seconds=args.wait_seconds,
    )

    if args.set_model:
        if before.get(
            "state"
        ) != "ready":
            print(
                json.dumps(
                    before,
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 0

        try:
            provider.set_model(
                args.set_model,
            )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "state": "error",
                        "message": "Could not change image model.",
                        "models": before.get(
                            "models",
                            [],
                        ),
                        "current_model": before.get(
                            "current_model",
                            "",
                        ),
                        "current_model_title": before.get(
                            "current_model_title",
                            "",
                        ),
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 0

        before = provider.status()

    print(
        json.dumps(
            before,
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(
        main()
    )
