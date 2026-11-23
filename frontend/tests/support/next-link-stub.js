import React from "react";

export default function Link({ href, children, ...rest }) {
  const target = typeof href === "string" ? href : "#";
  return React.createElement("a", { href: target, ...rest }, children);
}
