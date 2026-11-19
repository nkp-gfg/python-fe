export function useRouter() {
  return { push() {}, replace() {}, prefetch() {}, back() {} };
}

export function usePathname() {
  return "/flights";
}

export function useSearchParams() {
  return new URLSearchParams();
}
