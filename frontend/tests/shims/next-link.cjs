const React = require("react");

function Link({ href, children, ...rest }) {
  const url = typeof href === "string" ? href : "";
  return React.createElement("a", { href: url, ...rest }, children);
}

module.exports = Link;
module.exports.default = Link;
