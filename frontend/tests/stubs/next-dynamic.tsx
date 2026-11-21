import { useEffect, useState, type ComponentType } from "react";

type Loader<P> = () => Promise<ComponentType<P> | { default: ComponentType<P> }>;

/**
 * Stand-in for next/dynamic that still runs the real dynamic import.
 * The loaded module is the product component, not a fake panel.
 */
export default function dynamic<P extends object>(
  loader: Loader<P>,
  options?: { loading?: ComponentType },
) {
  const Loading = options?.loading;
  return function DynamicallyLoaded(props: P) {
    const [Comp, setComp] = useState<ComponentType<P> | null>(null);
    useEffect(() => {
      let cancelled = false;
      loader()
        .then((mod) => {
          const resolved = typeof mod === "function" ? mod : mod.default;
          if (!cancelled) setComp(() => resolved);
        })
        .catch(() => {
          /* The tab error boundary reports a failed panel. The header stays mounted. */
        });
      return () => {
        cancelled = true;
      };
    }, []);
    if (!Comp) return Loading ? <Loading /> : null;
    return <Comp {...props} />;
  };
}
