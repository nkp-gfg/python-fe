/** Records the option object the analytics charts pass into echarts. */

export function init(host) {
  const api = {
    setOption(option) {
      host.__chartOption = option;
    },
    resize() {},
    dispose() {},
    showLoading() {},
    hideLoading() {},
  };
  host.__chartApi = api;
  return api;
}

export function getInstanceByDom(host) {
  return host?.__chartApi ?? null;
}

export function use() {}
