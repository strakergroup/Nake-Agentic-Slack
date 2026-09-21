// Arbitr voice rules that can be checked mechanically. Pure logic, no DOM.
const RULES = [
  { rule: 'no-dash', re: /[–—]/ },
  { rule: 'no-emoji', re: /\p{Extended_Pictographic}/u },
  { rule: 'capitalise-arbitr', re: /(?<![\/\w.-])arbitr(?![\w-])/ },
  { rule: 'no-exclamation', re: /!/ },
  { rule: 'no-users', re: /\busers?\b/i },
  { rule: 'no-we', re: /\b(We|we|Our|our|ours)\b/ },
  { rule: 'retired-word', re: /\b(seamless(ly)?|empower(s|ed|ing)?|unlock(s|ed|ing)?|effortless(ly)?|bottleneck|raw potential|revolutionary|next-gen)\b/i },
  { rule: 'off-limits-claim', re: /\b(trust scor(e|ing)|consensus voting|glass box)\b/i },
  { rule: 'retired-name', re: /\b(Straker\.AI|NotVerify|LanguageCloud|RAY Translate|Connect to Verify)\b/ },
];

export function checkCopy(text) {
  return RULES.filter(({ re }) => re.test(text)).map(({ rule }) => ({ rule, text }));
}
