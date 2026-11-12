# yt-dlp-assify

A [yt-dlp](https://github.com/yt-dlp/yt-dlp) post-processor plugin that automatically converts YouTube `srv3` / `ytt` formatted subtitles into styled `.ass` subtitles using [YTSubConverter](https://github.com/arcusmaximus/YTSubConverter).

This allows embedding fully styled, formatted, and positioned YouTube subtitles directly into `.mkv` files or saving standalone `.ass` subtitle files.

## Installing

Requires yt-dlp `2023.01.02` or higher.

YTSubConverter must be present on your system.

You can download the `.whl` file from the [latest release](https://github.com/nateify/yt-dlp-assify/releases/latest) and place the zip file in a [supported plugin directory](https://github.com/yt-dlp/yt-dlp#installing-plugins).

### pip/pipx/uv

```
pipx inject yt-dlp yt-dlp-assify
```

```
python -m pip install -U yt-dlp-assify
```

```
uv tool install --reintall yt-dlp --with yt-dlp-assify
```

### Installing YTSubConverter

#### Windows
- **WinGet**: `winget install arcusmaximus.ytsubconverter`
- **Scoop**: `scoop bucket add extras && scoop install ytsubconverter`
- **Manual**: Download `YTSubConverter.exe` from [Releases](https://github.com/arcusmaximus/YTSubConverter/releases/latest) and place it in your `PATH`.

#### Linux

- **Debian / Ubuntu**: Install the official `.deb` package.
- **Fedora / RHEL**: Install the official `.rpm` package.
- **Arch Linux**: Install `ytsubconverter-bin` from the AUR.
- **Manual**: Extract `YTSubConverter-Linux.tar.xz` to `~/.local/bin` or `/usr/local/bin`.

#### macOS
- Download `YTSubConverter-macOS.dmg` for either `x64` or `arm64` (Apple Silicon).
- Drag `YTSubConverter.app` to your `/Applications` directory.

## Usage

The following options are required:
- `--sub-format` set to `srv3` and/or `ytt`
- `--embed-subs`
- `--remux-video mkv` or `--merge-output-format mkv` depending on the format selection.
- `--use-postprocessor Assify`

Example:

```sh
yt-dlp \
  --sub-langs "all,-live_chat" \
  --sub-format "srv3/ytt" \
  --use-postprocessor Assify \
  --embed-subs \
  --remux-video mkv \
  "https://www.youtube.com/watch?v=VIDEO_ID"
```

### Options

Options can be passed directly to the post-processor in key-value format via `--use-postprocessor "Assify:key=value"`. Multiple options can be combined using semicolons:

#### Specifying the YTSubConverter Executable Path
If `YTSubConverter` is not located in standard search paths or `PATH`, pass the `path` option:
```sh
yt-dlp --use-postprocessor "Assify:path=/custom/path/to/YTSubConverter" ...
```

#### Visually Accurate Positioning (`--visual`)
YTSubConverter supports a `--visual` flag for visually accurate positioning. Pass `visual=true`:
```sh
yt-dlp --use-postprocessor "Assify:visual=true" ...
```

> **Note:** When this option is enabled, the resulting `.ass` file cannot be converted back to SRV3.

#### Conversion Mode
Controls how subtitle tracks are converted based on whether they contain styles:
```sh
yt-dlp --use-postprocessor "Assify:mode=auto" ...
```

* **`auto`** *(Default)*: Inspects the SRV3 XML content. Converts **styled** subtitles (karaoke, custom fonts, colors, positioning) to `.ass` via `YTSubConverter`, and converts **unstyled** (plain dialogue) subtitles to `.srt`.
* **`always_ass`**: Converts all SRV3/YTT tracks to `.ass`, even if unstyled (legacy plugin behavior).
* **`only_styled`**: Converts styled tracks to `.ass`, but leaves unstyled tracks as original SRV3.

#### Subtitle Display Cap
When converting unstyled SRV3 subtitles to `.srt`, YouTube occasionally omits the duration (`d`) attribute on caption cues. 

You can pass `max_duration_cap` in milliseconds to prevent captions from remaining frozen on screen during long pauses or music breaks:
```sh
# Default: Set to 0 to disable the cap, matching YouTube's behavior
yt-dlp --use-postprocessor "Assify:max_duration_cap=0" ...

# Set a 5 second cap
yt-dlp --use-postprocessor "Assify:max_duration_cap=5000" ...
```