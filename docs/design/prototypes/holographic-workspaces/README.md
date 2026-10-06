# Holographic workspace review

Run `node docs/design/prototypes/holographic-workspaces/serve.cjs` from an isolated checkout. Open http://127.0.0.1:3193/#desktop. Ports 3192 and 3193 must be free. Node and Git are required; the source history must contain scene baseline `ed30ed8431b0ed81dd4160a098aa95c990fd0b58`.

This is a standalone product review, with synthetic records and no application backend or model. It covers the workspace registry and shared surface inventory. Friday's existing renderer and spatial Files engine run against a loopback fixture server; the scene stays mounted across ordinary navigation. The authoritative production shell is not replaced by this prototype.

Scene & depth provides the original 15 forms, Flat/Quiet/Balanced/Immersive depth, a camera-free pointer preview, explicit camera tracking, and Companion/Present/Focus arrangements. Camera capture is owned by the original scene. Off and hiding revoke startup intent. The background scene pauses while spatial Files is active. Reduced motion suppresses travel and decorative workspace motion.

Publishing, messages, model execution, account connections and generated content are demonstrations. Actual camera tracking requires browser permission, usable camera hardware and the original tracking assets. Source access remains limited to synthetic fixture records. The reusable workspace depth component is also available under `static/friday_workspace_depth.js` and `.css`.
