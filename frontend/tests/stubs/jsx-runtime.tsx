import {
  Fragment,
  jsx as realJsx,
  jsxs as realJsxs,
} from "../../node_modules/react/cjs/react-jsx-runtime.development.js";

export { Fragment };

export const capturedChartOptions: Array<{
  yAxis?: { data?: unknown };
  series?: Array<{ data?: unknown }>;
}> = [];

function record<T>(
  type: T,
  props: { option?: { yAxis?: { data?: unknown } } } | null,
  key: string | undefined,
  real: (type: T, props: typeof props, key: string | undefined) => unknown,
) {
  const data = props?.option?.yAxis?.data;
  if (Array.isArray(data) && props?.option) capturedChartOptions.push(props.option);
  return real(type, props, key);
}

export function jsx(
  type: unknown,
  props: { option?: { yAxis?: { data?: unknown } } } | null,
  key?: string,
) {
  return record(type, props, key, realJsx);
}

export function jsxs(
  type: unknown,
  props: { option?: { yAxis?: { data?: unknown } } } | null,
  key?: string,
) {
  return record(type, props, key, realJsxs);
}
