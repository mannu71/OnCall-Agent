// Config-loading fixture for codegraph indexing tests.
export function parseConfig(text) {
  return JSON.parse(text);
}

export class Loader {
  load(path) {
    return parseConfig(readFile(path));
  }
}

function readFile(p) {
  return "";
}
