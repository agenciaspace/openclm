const { test, expect } = require('@playwright/test');

const password = 'local-test-password-2026';
async function signIn(page) {
  await page.goto('./');
  await page.locator('#login [name=email]').fill('admin@example.com');
  await page.locator('#login [name=password]').fill(password);
  await page.locator('#login button[type=submit]').click();
  await expect(page.locator('#organization-switch')).toBeVisible();
}

test('public signup, recovery codes, second organization and isolated switch', async ({ page }, testInfo) => {
  const email = `public-${testInfo.project.name}@example.com`;
  await page.goto('./');
  await page.getByRole('button', { name: 'Criar conta gratuita', exact: true }).click();
  await page.locator('#register [name=name]').fill('Organization Owner');
  await page.locator('#register [name=email]').fill(email);
  await page.locator('#register [name=password]').fill(password);
  await page.locator('#register [name=organization_name]').fill('First organization');
  await page.locator('#register button[type=submit]').click();
  await expect(page.locator('.recovery-codes code')).toHaveCount(6);
  const recoveryCode = await page.locator('.recovery-codes code').first().textContent();
  await page.locator('#continue-account').click();
  await expect(page.locator('#organization-switch option')).toHaveCount(1);
  await expect(page.locator('#empty-new')).toBeVisible();
  const firstOrg = await page.locator('#organization-switch').inputValue();
  await page.locator('#create-organization').click();
  await page.locator('#new-organization [name=name]').fill('Second organization');
  await page.locator('#new-organization button[type=submit]').click();
  await expect(page.locator('#organization-switch option')).toHaveCount(2);
  await expect(page.locator('#organization-switch option:checked')).toContainText('Second organization');
  await page.locator('#organization-switch').selectOption(firstOrg);
  await expect(page.locator('#organization-switch option:checked')).toContainText('First organization');
  await expect(page.locator('#empty-new')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  // Recover using a saved code in a fresh anonymous session.
  await page.context().clearCookies();
  await page.reload();
  await page.getByRole('button', { name: 'Recuperar acesso' }).click();
  await page.locator('#recover [name=email]').fill(email);
  await page.locator('#recover [name=code]').fill(recoveryCode);
  await page.locator('#recover [name=password]').fill('replacement-browser-password-2026');
  await page.locator('#recover button[type=submit]').click();
  await expect(page.locator('.recovery-codes code')).toHaveCount(6);
  await page.locator('#continue-account').click();
  await page.locator('#login [name=email]').fill(email);
  await page.locator('#login [name=password]').fill('replacement-browser-password-2026');
  await page.locator('#login button[type=submit]').click();
  await expect(page.locator('#organization-switch option')).toHaveCount(2);
});

test('invite accepted by its recipient; role change and removal take effect', async ({ page, browser }, testInfo) => {
  await signIn(page);
  await page.locator('[data-view=settings]').click();
  await page.locator('#new-user').click();
  const email = `invited-${testInfo.project.name}@example.com`;
  await page.locator('#invite-form [name=email]').fill(email);
  await page.locator('#invite-form [name=role]').selectOption('viewer');
  await page.locator('#invite-form button[type=submit]').click();
  const link = page.locator('#invitation-link');
  await expect(link).toBeVisible();
  const invitationURL = await link.inputValue();
  await page.getByRole('button', { name: 'Fechar', exact: true }).click();
  const context = await browser.newContext({ viewport: page.viewportSize() });
  const guest = await context.newPage();
  try {
    await guest.goto(invitationURL);
    await guest.getByRole('button', { name: 'Criar conta gratuita', exact: true }).click();
    await guest.locator('#register [name=name]').fill('Invited Reader');
    await guest.locator('#register [name=email]').fill(email);
    await guest.locator('#register [name=password]').fill(password);
    await expect(guest.locator('#register [name=organization_name]')).toHaveCount(0);
    await guest.locator('#register button[type=submit]').click();
    await guest.locator('#continue-account').click();
    await guest.getByRole('button', { name: 'Aceitar convite', exact: true }).click();
    await expect(guest.locator('#organization-switch option')).toHaveCount(1);
    await expect(guest.locator('#new-contract')).toHaveCount(0);
    await page.locator('[data-view=settings]').click();
    const row = page.getByRole('row').filter({ hasText: email });
    await row.locator('select').selectOption('editor');
    await expect(page.locator('#toast')).toHaveText('Perfil atualizado.');
    await guest.reload();
    await expect(guest.locator('#new-contract')).toBeVisible();
    await row.getByRole('button', { name: 'Remover', exact: true }).click();
    await page.locator('#confirm-member button[type=submit]').click();
    await expect(page.locator('#toast')).toHaveText('Membro removido.');
    await guest.reload();
    await expect(guest.locator('#login')).toBeVisible();
  } finally {
    await context.close();
  }
});
