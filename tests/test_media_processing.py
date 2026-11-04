from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from amca.media_processing import (
    LocalMediaExtraction,
    LocalTextChunk,
    _run,
    local_media_runtime_status,
    ocr_image,
    transcribe_audio,
)


class LocalMediaProcessingTests(unittest.TestCase):
    def test_runtime_status_requires_exact_local_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            whisper = root / "whisper"
            whisper.mkdir()
            (whisper / "whisper-cli.exe").write_bytes(b"exe")
            missing_model = local_media_runtime_status(root)
            (whisper / "ggml-tiny.en.bin").write_bytes(b"model")
            complete = local_media_runtime_status(root)

        self.assertFalse(missing_model.transcription_available)
        self.assertTrue(complete.transcription_available)
        self.assertFalse(complete.to_dict()["network_used"])

    def test_ocr_is_bounded_and_provenance_labeled(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"Invoice total 42\n", stderr=b""
        )
        with patch(
            "amca.media_processing._tesseract_executable",
            return_value=Path("C:/Tesseract-OCR/tesseract.exe"),
        ), patch("amca.media_processing._run", return_value=completed) as runner:
            result = ocr_image(
                b"validated-image", suffix=".png", tools_root="C:/AMCA/tools"
            )

        self.assertEqual(result.status, "ocr_extracted")
        self.assertEqual(result.chunks[0].locator, "image:1/ocr:eng")
        self.assertEqual(result.chunks[0].text, "Invoice total 42")
        command = runner.call_args.args[0]
        self.assertTrue(command[1].endswith(".png"))
        self.assertIn("stdout", command)
        self.assertNotIn("http", " ".join(command).casefold())

    def test_whisper_json_preserves_timestamped_segments(self) -> None:
        def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
            output = Path(command[command.index("-of") + 1]).with_suffix(".json")
            output.write_text(
                json.dumps(
                    {
                        "transcription": [
                            {
                                "timestamps": {
                                    "from": "00:00:00,000",
                                    "to": "00:00:01,250",
                                },
                                "text": " local evidence",
                            },
                            {
                                "timestamps": {
                                    "from": "00:00:01,250",
                                    "to": "00:00:02,000",
                                },
                                "text": "[BLANK_AUDIO]",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, b"", b"")

        with patch(
            "amca.media_processing._whisper_files",
            return_value=(Path("C:/AMCA/whisper-cli.exe"), Path("C:/AMCA/model.bin")),
        ), patch("amca.media_processing._run", side_effect=fake_run) as runner:
            result = transcribe_audio(
                b"validated-audio", suffix=".wav", tools_root="C:/AMCA/tools"
            )

        self.assertEqual(result.status, "transcript_extracted")
        self.assertEqual(result.chunks[0].locator, "time:00:00:00.000-00:00:01.250")
        self.assertEqual(result.chunks[0].text, "local evidence")
        self.assertEqual(len(result.chunks), 1)
        command = runner.call_args.args[0]
        self.assertIn("-ng", command)
        self.assertIn("-ojf", command)
        self.assertNotIn("-d", command)
        self.assertNotIn("http", " ".join(command).casefold())

    def test_subprocess_boundary_disables_shell_and_sanitizes_environment(self) -> None:
        completed = subprocess.CompletedProcess([], 0, b"ok", b"")
        with patch("amca.media_processing.subprocess.run", return_value=completed) as run:
            result = _run(["safe.exe", "input"], timeout=1)

        self.assertIs(result, completed)
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertNotIn("USERPROFILE", run.call_args.kwargs["env"])
        self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_unsupported_audio_stays_truthfully_metadata_only(self) -> None:
        result = transcribe_audio(
            b"validated-audio", suffix=".m4a", tools_root="C:/AMCA/tools"
        )
        self.assertEqual(result.status, "metadata_only")
        self.assertEqual(result.chunks, ())
        self.assertIn("accepts only", result.note)


if __name__ == "__main__":
    unittest.main()
