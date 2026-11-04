// Accept a code or copied invitation URL. Also recover links previously encoded
// as route parameters; authorization/status checks still belong to the backend.
export function invitationToken(input: string): string {
  let value = input.trim();
  for (let count = 0; count < 3 && value.includes("%"); count++) {
    try {
      const decoded = decodeURIComponent(value);
      if (decoded === value) break;
      value = decoded;
    } catch { break; }
  }
  try {
    const url = new URL(value);
    if (url.protocol === "http:" || url.protocol === "https:") {
      const match = /^\/invitations\/([A-Za-z0-9_-]+)\/?$/.exec(url.pathname);
      if (match) return match[1];
    }
  } catch { /* Plain codes are already the expected API input. */ }
  return value;
}
