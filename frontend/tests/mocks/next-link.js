import { createElement } from "react";

export default function Link({ children, href, ...props }) {
  return createElement("a", { href: typeof href === "string" ? href : "#", ...props }, children);
}
