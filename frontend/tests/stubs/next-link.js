import React from "react";

export default function Link({ href, children, ...rest }) {
  const safe = { ...rest };
  delete safe.prefetch;
  delete safe.scroll;
  delete safe.replace;
  delete safe.passHref;
  delete safe.legacyBehavior;
  return React.createElement(
    "a",
    { href: typeof href === "string" ? href : "#", ...safe },
    children,
  );
}
