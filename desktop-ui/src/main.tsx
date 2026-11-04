import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";
import "./panel.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

import "./studio.css";
// Model settings design system. Imported last so its scoped tokens and layout
// win over the shared stylesheets without either having to know about the other.
import "./modelSettings.css";
