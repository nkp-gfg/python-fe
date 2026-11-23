import React, { useEffect, useState } from "react";

export default function dynamic(loader, options) {
  function DynamicChart(props) {
    const [Comp, setComp] = useState(null);
    useEffect(() => {
      let live = true;
      Promise.resolve()
        .then(() => loader())
        .then((mod) => {
          const Loaded = mod.default || mod;
          if (live) setComp(() => Loaded);
        });
      return () => {
        live = false;
      };
    }, []);
    if (!Comp) {
      const Loading = options && options.loading;
      return Loading ? React.createElement(Loading, {}) : null;
    }
    return React.createElement(Comp, props);
  }
  return DynamicChart;
}
