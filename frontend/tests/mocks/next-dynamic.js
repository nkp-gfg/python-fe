import React from "react";

export default function dynamic(_loader, options) {
  const Loading = options && options.loading;
  function DynamicStub(props) {
    return Loading ? React.createElement(Loading, props) : null;
  }
  return DynamicStub;
}
