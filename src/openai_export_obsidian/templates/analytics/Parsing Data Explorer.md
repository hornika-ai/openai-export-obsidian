---
type: parsing_data_explorer
analytics_schema: parsing-data-explorer-v1
---

# Parsing Data Explorer

```dataviewjs
const base = dv.current().file.folder;
const source = await dv.io.load(`${base}/app/view.js`);
if (!source) {
  dv.paragraph("Parsing Data Explorer is not installed correctly: app/view.js is missing.");
} else {
  const render = (0, eval)(`(${source})`);
  await render({ dv, app, base });
}
```
