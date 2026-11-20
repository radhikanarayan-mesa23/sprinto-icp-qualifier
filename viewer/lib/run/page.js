// What a fetched page means: is it a barrier, an error, or usable? Shared by the homepage and every extra page.
const CHALLENGE = /just a moment\.\.\.|cf-chl|attention required.{0,40}cloudflare|checking your browser/i;
const CAPTCHA = /g-recaptcha|h-captcha|hcaptcha|captcha-container|are you a robot|verify you are human/i;
export const LOGIN = /type=["']password["']|sign in to continue|log in to continue|please (log|sign) in/i;
export const BARRIERS = new Set(["captcha", "cloudflare_challenge", "login_wall"]); // stop poking at the site; never bypass

export const MESSAGES = {
  robots_disallowed: "robots.txt asks bots not to fetch this page, so the agent did not.",
  not_found_404: "The homepage returned 404.",
  captcha: "A CAPTCHA is in the way. The agent stops at barriers and never tries to get past them.",
  cloudflare_challenge: "A bot-protection challenge is in the way. The agent stops there and does not try to bypass it.",
  login_wall: "The page is behind a login. The agent never logs in.",
  blocked_address: "That address resolves to a private or internal network, which is not allowed.",
  redirect_off_domain: "The site redirects to a different domain, so the agent stopped rather than guess which company this is.",
  not_html: "The homepage is not an HTML page.",
  too_many_redirects: "The homepage redirected too many times.",
  timeout: "The site did not answer in time. Try again later.",
};

export const failureOf = (page) => {
  if (page.error) return page.error;
  if (page.status === 404 || page.status === 410) return "not_found_404";
  if ([401, 403].includes(page.status)) return `blocked_${page.status}`;
  if (page.status === 429 || page.status >= 500) return `transient_${page.status}`;
  if (page.status >= 400) return `http_${page.status}`;
  if (CHALLENGE.test(page.text.slice(0, 6000))) return "cloudflare_challenge";
  if (CAPTCHA.test(page.text)) return "captcha";
  return null;
};

export const explain = (reason) => MESSAGES[reason] ??
  (reason.startsWith("blocked_") ? `The site refused the request (HTTP ${reason.slice(8)}), usually bot protection. The agent stops at the block and does not try to get past it.`
  : reason.startsWith("transient_") ? `The site had a temporary problem (HTTP ${reason.slice(10)}). Try again later.`
  : reason.startsWith("network:") ? "The site could not be reached. Try again later."
  : reason);
