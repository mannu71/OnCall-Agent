// Client-call fixture for codegraph cross-service tests.
export function loadUsers() {
  return fetch("/users");
}
