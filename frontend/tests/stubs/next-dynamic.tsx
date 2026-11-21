import React from "react";

type Loader = () => Promise<unknown>;

/** Resolve like next/dynamic, but keep the server placeholder out of the way.
 * The chart option is already on this component's props when the page renders.
 */
export default function dynamic(_loader: Loader, options?: { loading?: () => React.ReactNode }) {
  function DynamicComponent(props: { option?: unknown }) {
    void props;
    const Loading = options?.loading;
    return Loading ? <Loading /> : null;
  }
  return DynamicComponent;
}
