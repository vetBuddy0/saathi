# How to start

## Once, on the Ubuntu machine

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
sudo apt install -y alsa-utils sox chromium-browser git pipewire pipewire-pulse
npm install -g @anthropic-ai/claude-code

mkdir saathi && cd saathi && git init
# SPEC.md and CLAUDE.md in the root
git add . && git commit -m "Spec"
claude
```

---

## Checkpoint 1

> Read SPEC.md and CLAUDE.md first.
>
> Build checkpoint 1 only. Done when I press the spacebar and the face reacts
> in under 100 ms, and `python -m saathi.smoke` correctly reports what is
> plugged into this machine.
>
> Build: repo layout per the module map (empty modules with docstrings where
> checkpoint 1 doesn't fill them) · `uv` and `pyproject.toml` · CI on
> ubuntu-latest and ubuntu-24.04-arm · the undefined-name check ·
> `audio/devices.py` with hotplug · `screen/server.py` · three faces behind one
> `Face` interface, Web-Eye-Animation vendored for the eyes · spacebar driving
> the state machine on fake events · `identity/store.py` with the schema and
> nothing but create/append/read · `tools/registry.py` and `stubs.py` going
> through the permission check · `smoke.py` and the `saathi` CLI.
>
> Don't build: voice engines, transport, retrieval, reflection, scheduler, AEC.
>
> Stop and tell me when CI is green.

## Checkpoint 2 — not until the face feels right

> Build checkpoint 2. Done when I can talk to it on either engine and it's
> recognisably the same character, every turn logged and within budget.
>
> AEC first with the bench test — everything depends on it. Then
> `VoiceSession` with realtime and cascade. `Transport` with websocket and
> local; no livekit. `identity/compile.py` running between turns. The handoff
> tool. The `turns` table and the budget test.

## Checkpoint 3

> Build checkpoint 3. Done when I talk to it, come back a week later, and it
> knows what mattered.
>
> Retrieval on recency + importance + relevance, per Generative Agents.
> `reflect.py` clustering episodes into rules with provenance. `profile.py`
> family-visible and editable. The scheduler, and the policy for staying quiet.

---

## What I actually review

After checkpoint 1, two things:

1. Press the spacebar. **Does the face react before I've finished pressing it?**
   Not "does it work" — does it feel instant.
2. Unplug the mic. Does it notice and say something useful on screen?

Then report in ordinary words: *"the blink looks mechanical"*, *"it reacts but
feels late"*, *"I couldn't tell it had noticed me"*. Those become the next
tasks. Logs say what happened; only I can say what it was like.

---

## What quietly rots

- **Latency.** Creeps. The budget test is the only defence — if it's ever
  disabled "temporarily", it's gone.
- **Device names.** The first hardcoded `plughw:` is when portability dies.
  Grep for it.
- **The five interfaces.** Reaching through one removes the architecture and
  nobody notices for weeks.
- **Status text.** It will reappear.
- **"Verified manually."** Not a test.

## The thing that beats all of it

Twenty minutes with one person in her seventies where **I say nothing and
watch.** Every hesitation, repetition or glance at me for rescue is a real
defect. Worth more than a month of opinions.
