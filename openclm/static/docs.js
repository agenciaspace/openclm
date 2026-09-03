"use strict";
async function docs() {
  const response = await fetch("/openapi.json");
  if (!response.ok) throw new Error("Especificação indisponível.");
  const spec = await response.json();
  const target = document.querySelector("#endpoints");
  target.replaceChildren();
  for (const [path, operations] of Object.entries(spec.paths))
    for (const [method, operation] of Object.entries(operations)) {
      const details = document.createElement("details");
      details.className = "endpoint";
      const summary = document.createElement("summary");
      const verb = document.createElement("span");
      verb.className = "method";
      verb.textContent = method.toUpperCase();
      summary.append(verb, document.createTextNode(path));
      const body = document.createElement("div");
      body.className = "endpoint-body";
      const title = document.createElement("h3");
      title.textContent = operation.summary;
      body.append(title);
      const pre = document.createElement("pre");
      pre.textContent = JSON.stringify(
        {
          parameters: operation.parameters,
          requestBody: operation.requestBody,
          responses: operation.responses,
          security: operation.security,
        },
        null,
        2,
      );
      body.append(pre);
      details.append(summary, body);
      target.append(details);
    }
  for (const [name, schema] of Object.entries(spec.components.schemas)) {
    const details = document.createElement("details");
    details.className = "endpoint";
    const summary = document.createElement("summary");
    summary.textContent = name;
    const pre = document.createElement("pre");
    pre.className = "endpoint-body";
    pre.textContent = JSON.stringify(schema, null, 2);
    details.append(summary, pre);
    document.querySelector("#schemas").append(details);
  }
}
docs().catch(
  (e) => (document.querySelector("#endpoints").textContent = e.message),
);
