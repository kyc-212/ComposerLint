# ComposerLint Setup for Windows x64

This graphical preview targets 64-bit Windows 11 on a local NTFS drive.

1. Verify the ZIP against the SHA-256 from the source that supplied it. In PowerShell, run `Get-FileHash -Algorithm SHA256 -LiteralPath '.\ComposerLint-Graphical-Setup-Windows-x64-preview.zip'`.
2. In File Explorer, check the verified ZIP's **Properties**. If Windows added an **Unblock** checkbox, select it before extracting this verified ZIP. Then choose **Extract All** into a new folder and keep all extracted files together.
3. Double-click **Open-ComposerLint-Setup.cmd** as your regular user. The launcher uses RemoteSigned for this PowerShell process. If your organization's policy still blocks the unsigned preview, contact your administrator. The terminal guide describes the alternate entry point.

Choose **Full experience** for notation and verified temporary edits, or **Text analysis only** to skip the renderer. Review both installation prompts. Full setup can reuse an approved renderer bundle or find tools for a local build. If a renderer tool is missing, setup explains the pinned portable download and asks for a decision. An optional local folder of reviewed files lets setup check and reuse those present. Missing files still require network access and approval. A changed local file causes setup to stop so you can check it.

After installation, copy the local MCP command and arguments into your AI client. In Full mode copy the supplied server-specific renderer setting. Keep the extracted folder if your client loads Skills from it. ComposerLint setup does not configure your AI client, use system-wide installation, or grant permission to export scores.

The preview supports cancellation during review prompts. During downloads or builds, leave the window open until the current setup operation finishes or the installer reports an error. Review the local log before sharing excerpts because it can show file paths.
