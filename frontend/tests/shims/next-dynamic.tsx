import { useEffect, useState, type ComponentType, type ReactNode } from "react";

type Loader = () => Promise<unknown>;

export default function dynamic(
  loader: Loader,
  options?: { loading?: ComponentType },
) {
  const Loading = options?.loading;
  return function Loaded(props: Record<string, unknown>) {
    const [Comp, setComp] = useState<ComponentType<Record<string, unknown>> | null>(null);
    useEffect(() => {
      let live = true;
      Promise.resolve(loader()).then((loaded) => {
        const component = (typeof loaded === "function"
          ? loaded
          : (loaded as { default?: unknown }).default) as ComponentType<Record<string, unknown>>;
        if (live) setComp(() => component);
      });
      return () => {
        live = false;
      };
    }, []);
    if (!Comp) return Loading ? <Loading /> : null;
    return <Comp {...props} />;
  };
}

export function noSSR(node: ReactNode) {
  return node;
}
