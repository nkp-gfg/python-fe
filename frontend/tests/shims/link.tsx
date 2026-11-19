import type { ReactNode } from "react";

export default function Link({
  href,
  children,
}: {
  href?: string;
  children?: ReactNode;
}) {
  return <a href={typeof href === "string" ? href : ""}>{children}</a>;
}
