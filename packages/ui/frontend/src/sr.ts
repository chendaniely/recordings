// shinyreact hooks for an app: read from the window.shinyreact global the server loads
// (shinyreact-build-app skill, Step 1). The npm package is imported for its TYPES only;
// `import type` is erased, so no second copy of React or the hooks can enter the bundle.
import type * as SR from "@posit-dev/shinyreact";

type Hooks = Pick<
  typeof SR,
  "useShinyInput" | "useShinyOutputValue" | "useShinyOutputStatus" | "useShinyInitialized"
>;

const shinyreact = (window as unknown as { shinyreact: Hooks }).shinyreact;

export const { useShinyInput, useShinyOutputValue, useShinyOutputStatus, useShinyInitialized } =
  shinyreact;
