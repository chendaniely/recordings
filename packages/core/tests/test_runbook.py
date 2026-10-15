import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RUNBOOK = REPO / "docs" / "runbooks" / "first-run.md"
# A fenced block whose fences start their own lines. Pairing fence lines (rather than any
# three backticks) keeps a json block from shifting every later block by one.
FENCE = re.compile(r"^[ \t]*```(\w*)[ \t]*\n(.*?)^[ \t]*```[ \t]*$", re.S | re.M)


def _text() -> str:
    return RUNBOOK.read_text(encoding="utf-8")


def _code_lines(text: str | None = None) -> list[str]:
    """Every line of the shell blocks (bash, sh or unmarked), in order."""
    return [line for lang, body in FENCE.findall(_text() if text is None else text)
            if lang in ("", "bash", "sh") for line in body.splitlines()]


def _section(heading: str) -> str:
    """The runbook from `heading` to the next heading of the same or a higher level. Fenced
    blocks are skipped while looking, because a shell comment also starts with #."""
    level = len(heading) - len(heading.lstrip("#"))
    inside, out = False, None
    for line in _text().splitlines():
        if re.match(r"\s*```", line):
            inside = not inside
        elif not inside and out is None and line == heading:
            out = []
            continue
        elif not inside and out is not None and re.match(rf"#{{1,{level}}} ", line):
            break
        if out is not None:
            out.append(line)
    assert out is not None, f"no heading {heading!r}"
    return "\n".join(out)


def test_the_runbook_covers_steps_one_to_four_and_a_real_restore():
    # why: whole lines, not substrings, so a heading quoted in prose or indented in a block
    # can't stand in for a real one.
    lines = set(_text().splitlines())
    for heading in ("## 1. Spikes", "### 1a. Server facts", "### 1b. NAS transport",
                    "### 1c. Before stage 2b (not needed for 2a)",
                    "## 2. Deploy 2a against an empty, initialised archive",
                    "## 3. Bring the source over", "## 4. Import", "## Restoring for real",
                    "## Checklist"):
        assert heading in lines, heading
    assert "| Fact | Answer |" in lines, "the Checklist table"


def test_the_runbook_holds_no_addresses_keys_or_tailnet_names():
    # why: the repo is public. The runbook uses variables and "the homelab server", never a
    # machine's real address or name.
    text = _text()
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text), "an IPv4 address"
    assert ".ts.net" not in text
    assert "BEGIN OPENSSH" not in text and "ssh-ed25519 AAAA" not in text


def test_no_secret_is_ever_typed_on_a_command_line():
    # why: shell history keeps command lines. Secrets go into files through an editor, and
    # tools that want one are given the file or asked at a prompt.
    for line in _code_lines():
        assert not re.search(r"\b(printf|echo)\b[^|]*>\s*\S*secrets/", line), line
        assert "RESTIC_PASSWORD=" not in line, line
        # RESTIC_REST_PASSWORD is fine when it is read from the file, never as a literal.
        assert not re.search(r'\bRESTIC_REST_PASSWORD=(?!"?\$)', line), line
        assert not re.search(r"\bcreate_user\s+\S+\s+\S", line), line  # user AND password
        assert not re.search(r"\bhtpasswd\b.*\s-[A-Za-z]*b", line), line  # -b: password as an argument
        assert not re.search(r"rest:https?://[^/\s]*:[^/\s]*@", line), line  # user:pass@ in a URL


def test_the_spikes_need_nothing_the_build_makes():
    # why: Dan runs the spikes while the build is still going, so they can't use `make
    # restic` (Task 11) or the image. Whatever they download is checked against its pin.
    lines = _code_lines(_section("## 1. Spikes"))
    for line in lines:
        assert not re.match(r"\s*(make|rc)\b", line), line
        assert ".cache/bin" not in line, line
        if "restic/rest-server:" in line:
            assert "@sha256:" in line, f"an image not pinned by digest: {line}"
    downloads = [i for i, line in enumerate(lines) if "releases/download/" in line]
    assert len(downloads) >= 3, "restic on the server, rest-server and restic on the NAS"
    for i in downloads:
        assert "sha256sum -c" in lines[i + 1], f"not checked: {lines[i]}"


def test_option_lists_are_arrays_so_zsh_and_bash_split_them_alike():
    # why: zsh doesn't split an unquoted $VAR into words, so `ssh $SSH_OPTS host` hands ssh one
    # long argument. An array expanded as "${NAME[@]}" splits the same way in both shells.
    lines = _code_lines()
    arrays = {m.group(1) for line in lines for m in re.finditer(r"\b([A-Z_]+)=\(", line)}
    assert {"SSH_OPTS", "CACERT", "IMPORT_MOUNTS"} <= arrays
    for line in lines:
        for name in arrays:
            assert not re.search(rf"\$\{{?{name}\b(?!\[)", line), line


def test_the_folders_are_private_before_anything_lands():
    # why: state.db keeps whole revisions of every recording.json, titles included, so it is
    # as private as the archive. Nobody but the owner gets into $RECORDINGS_HOME.
    lines = _code_lines(_section("### 1a. Server facts"))
    assert any('chmod 700 "$RECORDINGS_HOME"' in line and '"$RECORDINGS_HOME/state"' in line
               for line in lines)


def test_manual_runs_never_race_the_backup_loop():
    # why: `rc up -d` starts `ops --loop`. A manual backup beside it fights it for restic's
    # lock, and a loop backup could snapshot an import half done.
    running = False
    for line in _code_lines():
        if re.search(r"\brc (up -d|start backup)\b", line) or "systemctl reboot" in line:
            running = True
        elif re.search(r"\brc (stop backup|kill backup|down)\b", line):
            running = False
        elif re.search(r"\brc run --rm backup\b", line) or (
                "import-audio-router" in line and "--dry-run" not in line):
            assert not running, line
    # §20 asks for a killed backup service to raise the alert, and a stop isn't a kill.
    assert any(re.search(r"\brc kill backup\b", line) for line in _code_lines())


def test_the_imported_data_is_restore_tested_before_the_import_copy_goes():
    lines = _code_lines(_section("## 4. Import"))

    def first(pattern: str) -> int:
        found = [i for i, line in enumerate(lines) if re.search(pattern, line)]
        assert found, f"step 4 never runs {pattern!r}"
        return found[0]

    post = first(r"backup run --tag post-import")
    restored = first(r"recordings backup restore-test")
    deleted = first(r'rm -rf "\$RECORDINGS_HOME/import"')
    assert post < restored < deleted


def test_restoring_for_real_puts_every_piece_back():
    code = "\n".join(_code_lines(_section("## Restoring for real")))
    for needed in ('restore "$SNAP"', "--tag recordings", "--include",
                   "recordings reindex", "recordings init --adopt",
                   "/archive", "config.toml", "deploy.env", "state.db"):
        assert needed in code, needed


def test_every_recordings_command_the_runbook_names_parses():
    # why: a command renamed, or an option dropped, in a fix round must not leave the runbook
    # naming one that doesn't exist. Each `recordings …` command is parsed by the real CLI parser,
    # options and all: `doctor --role`, `init --adopt` and the restore steps included.
    import shlex

    from recordings.cli import build_parser

    # The fenced blocks, and the commands the prose names in `inline code` (`mirror --debug`).
    prose = FENCE.sub("", _text())
    lines = ("\n".join(_code_lines()).replace("\\\n", " ").splitlines()
             + re.findall(r"`([^`\n]+)`", prose))
    commands = []
    for line in lines:
        if not re.search(r"\brecordings [a-z]", line):
            continue
        # A placeholder for a number (`--max-delete N`) stands for any: 1.
        tokens = ["1" if t == "N" else t for t in shlex.split(line, comments=True)]
        for i, token in enumerate(tokens):
            # The command itself: first on the line, or after a service name, `run` or `exec`.
            # Not `--tag recordings` or a path.
            if token != "recordings" or (i and tokens[i - 1] not in ("web", "backup", "run",
                                                                       "exec")):
                continue
            command = []
            for t in tokens[i + 1:]:
                if t in ("|", "||", "&&", ";", "&") or t.startswith((">", "2>")):
                    break
                command.append(t)
            if command and re.fullmatch(r"[a-z][a-z-]*", command[0]):
                commands.append(command)
    unparsed = []
    for command in commands:
        try:
            build_parser().parse_args(command)
        except SystemExit:
            unparsed.append("recordings " + shlex.join(command))
    assert unparsed == []
    named = {c[0] for c in commands}
    assert {"init", "doctor", "backup", "mirror", "import-audio-router", "validate", "reindex",
            "alert-test"} <= named, sorted(named)
    assert any(c[0] == "init" and "--adopt" in c for c in commands)
    assert {"web", "backup"} <= {c[c.index("--role") + 1] for c in commands
                                 if c[0] == "doctor" and "--role" in c[:-1]}
    assert any(c[:2] == ["backup", "restore-test"] for c in commands)
    # Two commands the runbook names only in prose, where the parse used to skip them.
    assert ["mirror", "--max-delete", "1"] in commands and ["mirror", "--debug"] in commands
