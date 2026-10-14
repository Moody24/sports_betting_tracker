---
name: apple-hig-reference
description: Review an Edge Tracker web interface through a secondary Apple Human Interface Guidelines lens when explicitly requested.
---

# Apple HIG reference for Edge Tracker

Use only on explicit request for a UI review. This is original project guidance
informed by the [Apple Human Interface Guidelines](https://developer.apple.com/design/human-interface-guidelines/),
not a copy of an upstream skill or a claim that a Flask web page is a native app.
Read the current Apple guidance when a platform-specific detail matters.

First inspect the actual template, CSS, JS, state, and existing
[UI baseline](../../../docs/ui_v1_baseline.md). The project's Interface Design
skill and functional checks govern product screens; Taste covers public pages.
Preserve the current typography, horizontal navigation, status colors,
Flask/Jinja, and vanilla CSS/JS. Do not introduce Apple fonts, native-style
navigation, packages, hooks, API calls, or a whole-product redesign.

For the assigned screen, evaluate:

1. **Hierarchy:** Is the primary decision or action apparent, and are labels,
   spacing, and grouping consistent with the screen's job?
2. **Interaction:** Can controls be found and operated with keyboard and touch?
   Are focus, disabled, loading, success, and error states clear?
3. **Accessibility:** Check contrast, text scaling, semantics, input labels,
   errors, and reduced-motion behavior where relevant.
4. **Trust:** Do odds, timestamps, unavailable data, and recommended actions
   communicate their actual state without implying certainty?

Report concrete findings with file links, affected state and priority. Say which
points are verified in the current code and which need visual or device testing.
Propose the smallest changes compatible with the established product design.
Do not edit files unless the task separately authorizes implementation.
