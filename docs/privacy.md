# Privacy

VOLUM is local-first. This document states what that means concretely, so the claim can be
checked rather than trusted.

## What stays on your machine

Everything by default: input images, intermediate results, model inference, generated
meshes and textures, projects, job records, logs and the cache. All of it lives in your
local data directory. Nothing is uploaded.

## What VOLUM does not do

- No telemetry.
- No analytics.
- No crash reporting to a remote service.
- No user account, no login, no licence check.
- No remote inference. Models run on your hardware or not at all.
- No external image APIs — background removal, segmentation and analysis are local.

None of these exist behind a setting that defaults to on. They do not exist.

## When VOLUM uses the network

Three cases, all user-initiated:

1. **Downloading model weights**, when you press Install for a specific model. The
   download goes to the model host (typically Hugging Face). VOLUM shows what is being
   downloaded and how large it is beforehand.
2. **Gated model downloads**, where a model host requires an account and accepted terms —
   DINOv3 is one. In that case you supply a token; it is used for that download and is not
   transmitted anywhere else.
3. **Update checks**, if enabled. Application updates and model updates are separate.

After a model is installed it works **offline**. If you want to verify that, disconnect
the network and generate.

## The local engine listens on a port — here is exactly what that means

The desktop application talks to a local Python engine over HTTP. A program that opens a
port deserves a plain answer about who can reach it:

- It binds **`127.0.0.1` only**. Not `0.0.0.0`. Nothing outside your machine can connect —
  not another device on your network, not your router.
- The port is **chosen by the operating system** at start-up, not fixed, so nothing can sit
  and wait on a known port for VOLUM to appear.
- **Every request needs a session token**, including the health check. The desktop app
  generates it at launch, passes it to the engine through the environment, and it is never
  written to disk. A web page that guessed the port would still get `401` — and its browser
  would refuse the request before that, because only the desktop app's own origin is allowed.
- The engine **stops when the app does**. If the app crashes, the engine notices its parent
  is gone and exits rather than lingering.
- It serves your job records and generated files to the app on your machine. It sends
  nothing anywhere.

You can verify all of this: start it by hand (`README.md` → The engine), then `curl` it
without the token, and from another machine on your network.

## Your files

Original inputs are never modified. Preprocessing writes derivatives alongside them.
Nothing is deleted without an explicit action.

## Removing everything

The data directory is a plain directory. Deleting it removes every model, project, job and
cached artifact VOLUM has ever written. `volum cleanup` does the same, selectively.
