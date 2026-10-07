// Fail fast on the wrong Node: shinyreact builds and tests its JS with Node 22 (pkg-js/.nvmrc).
const major = Number(process.versions.node.split(".")[0]);
if (major !== 22) {
  console.error(`Node 22 is required (found ${process.versions.node}). Run: nvm use`);
  process.exit(1);
}
