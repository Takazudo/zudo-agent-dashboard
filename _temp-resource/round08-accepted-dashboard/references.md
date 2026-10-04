# Read-only source evidence

All references were read at pinned revisions. Source behavior is not evidence of
new visual or native-input verification in this prototype.

## zudo-doc-cloud
Revision: 4a833e2ae5972d99da2c14d06621d04bfc685a49

- Actual Enlarge editor / Restore button, Maximize/Minimize icons, aria-pressed:
  https://github.com/zudolab/zudo-doc-cloud/blob/4a833e2ae5972d99da2c14d06621d04bfc685a49/packages/zudo-doc-cloud/src/features/editor/workspace.tsx#L1978-L1990
- Existing workspace stays mounted; inert peers, focus/Tab/scroll restoration:
  https://github.com/zudolab/zudo-doc-cloud/blob/4a833e2ae5972d99da2c14d06621d04bfc685a49/packages/zudo-doc-cloud/src/features/editor/workspace.tsx#L750-L836
- Fixed enlarged geometry:
  https://github.com/zudolab/zudo-doc-cloud/blob/4a833e2ae5972d99da2c14d06621d04bfc685a49/packages/zudo-doc-cloud/src/features/editor/editor-chrome.css#L602-L625
- Source tests for retained DOM identity and focus, read but not executed:
  https://github.com/zudolab/zudo-doc-cloud/blob/4a833e2ae5972d99da2c14d06621d04bfc685a49/packages/zudo-doc-cloud/src/features/editor/__tests__/workspace.test.tsx#L1600-L1780

No separate generic textarea-expand widget was found. The supported match is this
actual editor-pane enlargement. Prototype enlargement fills the detail area and
remains independent of the outer inspector expansion.

## zudo-text
Revision: 77bd2623bdaae5b27a5fb72c3da365c1adabbb5b

- Real embedded EditorView, history, Vim and Compartments:
  https://github.com/zudolab/zudo-text/blob/77bd2623bdaae5b27a5fb72c3da365c1adabbb5b/tauri-app/renderer/components/embedded-code-editor.tsx#L427-L561
- Vim toggling without editor replacement:
  https://github.com/zudolab/zudo-text/blob/77bd2623bdaae5b27a5fb72c3da365c1adabbb5b/tauri-app/renderer/hooks/__tests__/use-codemirror-editor-vim-toggle.test.tsx#L75-L181
- Settings draft/cancel and actual Save & Reload behavior:
  https://github.com/zudolab/zudo-text/blob/77bd2623bdaae5b27a5fb72c3da365c1adabbb5b/tauri-app/renderer/components/settings/settings-dialog.tsx#L288-L366
  https://github.com/zudolab/zudo-text/blob/77bd2623bdaae5b27a5fb72c3da365c1adabbb5b/tauri-app/renderer/components/settings/settings-dialog.tsx#L578-L663
- Shared palette and separate light/dark semantic maps:
  https://github.com/zudolab/zudo-text/blob/77bd2623bdaae5b27a5fb72c3da365c1adabbb5b/packages/color-themes/src/color-themes.ts#L230-L478
- System setting vs effective mode and pre-paint stamping:
  https://github.com/zudolab/zudo-text/blob/77bd2623bdaae5b27a5fb72c3da365c1adabbb5b/packages/ui-components/src/color-mode.ts
- CodeMirror CSS variables and dark facet:
  https://github.com/zudolab/zudo-text/blob/77bd2623bdaae5b27a5fb72c3da365c1adabbb5b/tauri-app/renderer/components/editor-pane/editor-theme.ts
- Current terminal theme updates without recreating shell:
  https://github.com/zudolab/zudo-text/blob/77bd2623bdaae5b27a5fb72c3da365c1adabbb5b/tauri-app/renderer/components/terminal/terminal-view.tsx#L45-L180
- Selection contrast rationale:
  https://github.com/zudolab/zudo-text/blob/77bd2623bdaae5b27a5fb72c3da365c1adabbb5b/packages/color-themes/src/contrast-pair-matrix.ts#L1-L26

Prototype Apply/Cancel is an adaptation: no 300ms appearance preview and no reload.
No xterm/real shell was added. The mock capture remains static DOM output.

## zudo-css-wisdom
Revision: 6f7c31b2baa70bd188aea31ecbd5ac08ee420065

- https://github.com/Takazudo/zudo-css-wisdom/blob/6f7c31b2baa70bd188aea31ecbd5ac08ee420065/src/content/docs/color/dark-mode-strategies.mdx
- https://github.com/Takazudo/zudo-css-wisdom/blob/6f7c31b2baa70bd188aea31ecbd5ac08ee420065/src/content/docs/color/three-tier-color-strategy.mdx#L19-L29
- https://github.com/Takazudo/zudo-css-wisdom/blob/6f7c31b2baa70bd188aea31ecbd5ac08ee420065/src/content/docs/color/three-tier-color-strategy.mdx#L1288-L1451

No literal lightdarkstrategy filename exists in the inspected main tree. Relevant
notes define palette → semantic theme → local component aliases. Dark mode changes
semantic mappings. Exact prototype colors are tuned for the dashboard's text pairs,
not claimed as a pixel-identical copy.

## Bundled editor library

Official npm packages, exact versions in package-lock.json. Compose source is
editor-src/composer.js; dist/editor.js is the self-contained built bundle.
@codemirror/state 6.7.6, @codemirror/view 6.43.13,
@codemirror/commands 6.10.2, @replit/codemirror-vim 6.4.0.
Vim must precede default keymaps and drawSelection is included:
https://github.com/replit/codemirror-vim
