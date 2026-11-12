from __future__ import annotations

import html
import os
import pathlib
import shutil
import subprocess
import sys
import xml.etree.ElementTree
from typing import Any

from yt_dlp.postprocessor.common import PostProcessor


def is_styled_srv3(xml_root: xml.etree.ElementTree.Element) -> bool:
    """
    Inspect SRV3 XML to determine if it contains styles, positioning,
    custom colors, or karaoke that would be lost in SRT.
    """
    head = xml_root.find("head")
    if head is not None:
        for pen in head.findall("pen"):
            attrib = pen.attrib

            # Bold, italic, underline
            if attrib.get("b") == "1" or attrib.get("i") == "1" or attrib.get("u") == "1":
                return True

            # Custom font family
            if "fs" in attrib and attrib["fs"] not in ("0", ""):
                return True

            # Custom font size
            if "sz" in attrib and attrib["sz"] not in ("100", ""):
                return True

            # Custom text color
            fc = attrib.get("fc", "").upper()
            if fc and fc not in ("#FEFEFE", "#FFFFFF", "#FFF"):
                return True

            # Custom opacity
            if "fo" in attrib:
                try:
                    if int(attrib["fo"]) < 250:
                        return True
                except ValueError:
                    return True

            # Background box removed or colored
            if "bo" in attrib and attrib["bo"] not in ("200", "254", ""):
                return True
            bc = attrib.get("bc", "").upper()
            if bc and bc not in ("#000000", "#000"):
                return True

            # Edge type (glow, shadow, bevel) or custom edge color
            if "et" in attrib or "ec" in attrib:
                return True

            # Ruby text (furigana), vertical text, or subscript/superscript
            if "rb" in attrib or "of" in attrib or "hg" in attrib:
                return True

        # Window position elements
        for wp in head.findall("wp"):
            attrib = wp.attrib
            # Horizontal alignment: 50 is center
            if "ah" in attrib and attrib["ah"] != "50":
                return True
            # Vertical alignment: values < 75 are top or middle of screen
            if "av" in attrib:
                try:
                    if int(attrib["av"]) < 75:
                        return True
                except ValueError:
                    return True
            # Anchor point: 6, 7, 8 are bottom anchors
            if "ap" in attrib and attrib["ap"] not in ("6", "7", "8"):
                return True
            # Explicit row/column layout restrictions
            if "rc" in attrib or "cc" in attrib:
                return True

        # Window style elements
        for ws in head.findall("ws"):
            attrib = ws.attrib
            if "ju" in attrib and attrib["ju"] != "2":
                return True
            if "sd" in attrib or "wfo" in attrib:
                return True

    body = xml_root.find("body")
    if body is not None:
        for p in body.findall("p"):
            if "p" in p.attrib:
                return True
            if "wp" in p.attrib and p.attrib["wp"] not in ("0", "1"):
                return True

            for s in p.findall("s"):
                if "p" in s.attrib:
                    return True
                if "t" in s.attrib:
                    return True
                if "ac" in s.attrib:
                    return True

    return False


def _ms_to_srt_timestamp(ms: int) -> str:
    if ms < 0:
        ms = 0
    hours = ms // 3600000
    ms %= 3600000
    minutes = ms // 60000
    ms %= 60000
    seconds = ms // 1000
    milliseconds = ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def convert_srv3_to_srt(
    xml_content: str,
    max_duration_ms: int | None = 5000,
) -> tuple[str, dict[str, int]]:
    """
    Returns the formatted SRT string and a stats dictionary tracking modifications.
    """
    root = xml.etree.ElementTree.fromstring(xml_content)
    raw_cues: list[tuple[int, int | None, str]] = []

    for p in root.findall(".//p"):
        t_attr = p.attrib.get("t")
        if t_attr is None:
            continue
        try:
            start_ms = int(t_attr)
        except ValueError:
            continue

        d_attr = p.attrib.get("d")
        duration_ms = int(d_attr) if d_attr and d_attr.isdigit() else None

        # Recursively extract text across any child nodes
        raw_text = "".join(p.itertext())
        cleaned_text = html.unescape(raw_text).replace("\u200b", "").strip()
        if not cleaned_text:
            continue

        raw_cues.append((start_ms, duration_ms, cleaned_text))

    raw_cues.sort(key=lambda c: c[0])

    srt_entries: list[str] = []
    cue_index = 1
    total_cues = len(raw_cues)

    # Diagnostic counters
    stats = {
        "total_cues": total_cues,
        "missing_duration": 0,
        "capped_duration": 0,
        "zero_duration_fixed": 0,
    }

    for i, (start_ms, duration_ms, text) in enumerate(raw_cues):
        if duration_ms is not None and duration_ms > 0:
            end_ms = start_ms + duration_ms
        else:
            stats["missing_duration"] += 1
            if i + 1 < total_cues:
                next_start = raw_cues[i + 1][0]
                if max_duration_ms and 0 < max_duration_ms < (next_start - start_ms):
                    end_ms = start_ms + max_duration_ms
                    stats["capped_duration"] += 1
                else:
                    end_ms = next_start
            else:
                end_ms = start_ms + 2500

        if end_ms <= start_ms:
            end_ms = start_ms + 1000
            stats["zero_duration_fixed"] += 1

        start_time_str = _ms_to_srt_timestamp(start_ms)
        end_time_str = _ms_to_srt_timestamp(end_ms)

        srt_entries.append(f"{cue_index}\n{start_time_str} --> {end_time_str}\n{text}\n")
        cue_index += 1

    return "\n".join(srt_entries), stats


class AssifyPP(PostProcessor):
    """
    A yt-dlp post-processor plugin that converts YouTube srv3/ytt subtitles
    into stylized ASS format using YTSubConverter.
    """

    _downloader: Any = None

    def __init__(self, downloader: Any = None, **kwargs: Any) -> None:
        super().__init__(downloader)
        self._kwargs = kwargs
        self._executable_path: str | None = None

    @classmethod
    def _candidate_binary_names(cls) -> list[str]:
        if sys.platform == "win32":
            return ["YTSubConverter.exe", "ytsubconverter.exe", "YTSubConverter", "ytsubconverter"]
        return ["YTSubConverter", "ytsubconverter", "YTSubConverter.exe"]

    @classmethod
    def _fallback_search_paths(cls) -> list[pathlib.Path]:
        paths: list[pathlib.Path] = []

        if sys.platform == "darwin":
            paths.extend(
                [
                    pathlib.Path("/Applications/YTSubConverter.app/Contents/MacOS/YTSubConverter"),
                    pathlib.Path("~/Applications/YTSubConverter.app/Contents/MacOS/YTSubConverter").expanduser(),
                    pathlib.Path("/opt/homebrew/bin/ytsubconverter"),
                    pathlib.Path("/opt/homebrew/bin/YTSubConverter"),
                    pathlib.Path("/usr/local/bin/ytsubconverter"),
                    pathlib.Path("/usr/local/bin/YTSubConverter"),
                    pathlib.Path("~/.local/bin/YTSubConverter").expanduser(),
                    pathlib.Path("~/.local/bin/ytsubconverter").expanduser(),
                ],
            )
        elif sys.platform == "win32":
            user_profile = os.environ.get("USERPROFILE", "")
            local_appdata = os.environ.get("LOCALAPPDATA", "")
            program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
            program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
            if user_profile:
                paths.append(pathlib.Path(user_profile) / "scoop" / "shims" / "YTSubConverter.exe")
                paths.append(
                    pathlib.Path(user_profile) / "scoop" / "apps" / "ytsubconverter" / "current" / "YTSubConverter.exe",
                )
            if local_appdata:
                paths.append(pathlib.Path(local_appdata) / "Microsoft" / "WinGet" / "Links" / "YTSubConverter.exe")
            paths.append(pathlib.Path(program_files) / "WinGet" / "Links" / "YTSubConverter.exe")
            paths.append(pathlib.Path(program_files_x86) / "WinGet" / "Links" / "YTSubConverter.exe")
        else:
            paths.extend(
                [
                    pathlib.Path("/opt/ytsubconverter/ytsubconverter"),
                    pathlib.Path("/usr/bin/ytsubconverter"),
                    pathlib.Path("/usr/bin/YTSubConverter"),
                    pathlib.Path("/usr/local/bin/ytsubconverter"),
                    pathlib.Path("/usr/local/bin/YTSubConverter"),
                    pathlib.Path("~/.local/bin/YTSubConverter").expanduser(),
                    pathlib.Path("~/.local/bin/ytsubconverter").expanduser(),
                ],
            )

        return paths

    @classmethod
    def _resolve_target(cls, target: str | pathlib.Path) -> str | None:
        """Resolve a candidate string or Path to an existing executable."""
        path = pathlib.Path(os.path.expandvars(str(target))).expanduser()

        if path.is_file() and (sys.platform == "win32" or os.access(path, os.X_OK)):
            return str(path.resolve())

        if path.is_dir():
            # Check macOS app bundle layout
            macos_bin = path / "Contents" / "MacOS" / "YTSubConverter"
            if macos_bin.is_file() and os.access(macos_bin, os.X_OK):
                return str(macos_bin.resolve())

            # Check candidate names inside directory
            for name in cls._candidate_binary_names():
                sub = path / name
                if sub.is_file() and (sys.platform == "win32" or os.access(sub, os.X_OK)):
                    return str(sub.resolve())

        found = shutil.which(str(target))
        if found:
            return found

        return None

    def _get_option(self, key: str, default: Any = None) -> Any:
        if key in self._kwargs:
            return self._kwargs[key]

        return default

    def _get_bool_option(self, key: str, default: bool = False) -> bool:
        val = self._get_option(key, default)
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.strip().lower() in ("1", "true", "yes", "on", "y")
        return bool(val)

    def _resolve_executable_path(self) -> str | None:
        if self._executable_path:
            return self._executable_path

        user_path = (
            self._get_option("ytsubconverter_path")
            or self._get_option("path")
            or self._get_option("executable")
            or self._get_option("exec")
            or self._get_option("bin")
        )
        if user_path:
            resolved = self._resolve_target(user_path)
            if resolved:
                self.write_debug(f"Found user-specified YTSubConverter: {resolved}")
                self._executable_path = resolved
                return resolved
            self.report_warning(f'Configured YTSubConverter path "{user_path}" does not exist or is not executable')

        for bin_name in self._candidate_binary_names():
            found = shutil.which(bin_name)
            if found:
                self.write_debug(f"Found YTSubConverter in PATH: {found}")
                self._executable_path = found
                return found

        for candidate_path in self._fallback_search_paths():
            if candidate_path.is_file() and (sys.platform == "win32" or os.access(candidate_path, os.X_OK)):
                resolved = str(candidate_path.resolve())
                self.write_debug(f"Found YTSubConverter at: {resolved}")
                self._executable_path = resolved
                return resolved

        return None

    def _convert_to_ass(self, in_filepath: str, out_filepath: str, exe_path: str) -> bool:
        cmd = [exe_path]

        if self._get_bool_option("visual", default=False):
            cmd.append("--visual")

        ppa: dict[str, Any] = self.get_param("postprocessor_args") or {}
        extra_args = ppa.get("ytsubconverter") or ppa.get("assify") or []
        if isinstance(extra_args, list):
            cmd.extend(extra_args)

        cmd.extend([in_filepath, out_filepath])

        self.write_debug(f"Executing: {' '.join(cmd)}")
        try:
            startupinfo = None
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                startupinfo=startupinfo,
            )

            if result.returncode != 0:
                err_msg = (result.stderr or result.stdout or f"Exit code {result.returncode}").strip()
                self.report_warning(f'YTSubConverter failed for "{in_filepath}": {err_msg}')
                return False

            if not os.path.exists(out_filepath) or os.path.getsize(out_filepath) == 0:
                self.report_warning(
                    f'YTSubConverter completed but output file "{out_filepath}" is missing or empty',
                )
                return False

            return True
        except Exception as exc:
            self.report_warning(f"Error executing YTSubConverter: {exc}")
            return False

    def run(self, information: Any) -> tuple[list[str], Any]:
        subs = information.get("requested_subtitles")
        if not subs:
            return [], information

        mode = str(self._get_option("mode", "auto")).strip().lower()

        files_to_delete: list[str] = []
        exe_path: str | None = None

        for lang, sub in subs.items():
            if lang == "live_chat":
                continue

            sub_ext = sub.get("ext")
            sub_path = sub.get("filepath")

            if sub_ext not in ("srv3", "ytt"):
                continue

            if not sub_path or not os.path.exists(sub_path):
                continue

            try:
                with open(sub_path, encoding="utf-8", errors="replace") as f:
                    content = f.read()
                xml_root = xml.etree.ElementTree.fromstring(content)
            except Exception as e:
                self.report_warning(f'Could not parse subtitle XML for "{lang}": {e}')
                continue

            has_style = is_styled_srv3(xml_root)
            should_use_ass = (mode in ("always_ass", "ass")) or has_style

            if should_use_ass:
                if not exe_path:
                    exe_path = self._resolve_executable_path()
                    if not exe_path:
                        self.report_warning(
                            'YTSubConverter not found. Keeping SRV3. Set path via --extractor-args "assify:path=..."',
                        )
                        continue

                ass_path = str(pathlib.Path(sub_path).with_suffix(".ass"))
                self.to_screen(f"Converting styled {sub_ext.upper()} subtitle to ASS: {sub_path} -> {ass_path}")
                if self._convert_to_ass(sub_path, ass_path, exe_path):
                    sub["filepath"] = ass_path
                    sub["ext"] = "ass"
                    files_to_delete.append(sub_path)

            else:
                if mode == "only_styled":
                    self.write_debug(
                        f'Subtitle for "{lang}" is unstyled and mode="only_styled"; leaving as {sub_ext}.',
                    )
                    continue

                srt_path = str(pathlib.Path(sub_path).with_suffix(".srt"))
                self.to_screen(f"Converting unstyled {sub_ext.upper()} subtitle to SRT: {sub_path} -> {srt_path}")
                try:
                    cap_opt = self._get_option("max_duration_cap", default=0)
                    max_cap = int(cap_opt) if str(cap_opt).isdigit() else None

                    srt_data, stats = convert_srv3_to_srt(content, max_duration_ms=max_cap)

                    if stats["missing_duration"] > 0:
                        self.write_debug(
                            f"[{lang}] Synthesized end times for {stats['missing_duration']}/{stats['total_cues']} cue(s) "
                            f'(missing "d" in SRV3). '
                            f"Capped at {max_cap}ms: {stats['capped_duration']} cue(s). "
                            f"Zero/inverted fixes: {stats['zero_duration_fixed']} cue(s)."
                        )

                    with open(srt_path, "w", encoding="utf-8") as f:
                        f.write(srt_data)
                    sub["filepath"] = srt_path
                    sub["ext"] = "srt"
                    files_to_delete.append(sub_path)
                except Exception as e:
                    self.report_warning(f'Failed to convert unstyled subtitle "{sub_path}" to SRT: {e}')

        return files_to_delete, information


# Alias for backward compatibility
assifyPP = AssifyPP
