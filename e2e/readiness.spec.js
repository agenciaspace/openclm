const { test, expect } = require('@playwright/test');

async function noHorizontalOverflow(page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1);
}

for (const role of ['admin', 'editor', 'reviewer', 'viewer']) {
  test(`${role}: login, navigation, capabilities, logout`, async ({ page }) => {
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('./');
    await expect(page.locator('#login')).toBeVisible();
    await noHorizontalOverflow(page);
    await page.locator('[name=email]').fill(`${role}@example.com`);
    await page.locator('[name=password]').fill('local-test-password-2026');
    await page.getByRole('button', { name: 'Entrar no OpenCLM' }).click();
    await expect(page.locator('#search')).toBeVisible();
    await noHorizontalOverflow(page);
    for (const [view, title] of [['templates', 'Modelos e formulários'], ['workflows', 'Workflows'], ['settings', 'Configurações']]) {
      await page.locator(`[data-view=${view}]`).click();
      await expect(page.locator(`[data-view=${view}]`)).toHaveClass('active');
      await expect(page.locator('#content h1')).toHaveText(title);
      await noHorizontalOverflow(page);
    }
    await expect(page.locator('#new-user')).toHaveCount(role === 'admin' ? 1 : 0);
    if (['admin', 'editor'].includes(role)) {
      await expect(page.locator('#connect-docusign')).toBeDisabled();
    }
    const logout = page.locator('#top-logout');
    if (await logout.isVisible()) await logout.click();
    else await page.locator('#logout').click();
    await expect(page.locator('#login')).toBeVisible();
    expect((await page.request.get('/openclm/api/v1/contracts')).status()).toBe(401);
    expect(errors).toEqual([]);
  });
}

test('admin invites a reader; contract form generates a document and requires independent approval', async ({ page }, testInfo) => {
  await page.goto('./');
  await page.locator('[name=email]').fill('admin@example.com');
  await page.locator('[name=password]').fill('local-test-password-2026');
  await page.getByRole('button', { name: 'Entrar no OpenCLM' }).click();
  await page.locator('[data-view=settings]').click();
  await page.locator('#new-user').click();
  const member = `reader-${testInfo.project.name}@example.com`;
  await page.locator('#invite-form [name=email]').fill(member);
  await page.locator('#invite-form [name=role]').selectOption('viewer');
  await page.locator('#invite-form button[type=submit]').click();
  await expect(page.locator('#invitation-link')).toHaveValue(/#invite=/);
  await page.getByRole('button', { name: 'Fechar', exact: true }).click();
  await expect(page.locator('#pending-invitations').getByText(member, { exact: false })).toBeVisible();
  await page.locator('[data-view=contracts]').click();
  await page.locator('#new-contract').click();
  await page.locator('#choose-template').selectOption({ label: 'Prestação de serviços' });
  await page.locator('#contract-form [name=title]').fill(`Browser contract ${testInfo.project.name}`);
  await page.locator('#contract-form [name=counterparty]').fill('Synthetic counterparty');
  const answers = {
    party_name: 'Synthetic company', party_id: '00.000.000/0001-00',
    counterparty_name: 'Synthetic counterparty', counterparty_id: '11.111.111/0001-11',
    effective_date: '2026-10-13', term_months: '12', jurisdiction: 'São Paulo',
    scope: 'Synthetic browser acceptance test', fee: 'R$ 1.000,00'
  };
  for (const [key, value] of Object.entries(answers)) {
    await page.locator(`[name="answer:${key}"]`).fill(value);
  }
  await page.getByRole('button', { name: 'Gerar contrato' }).click();
  await expect(page.locator('.document-preview')).toContainText(answers.scope);
  await page.locator('[data-transition=submit]').click();
  await expect(page.locator('#modal .badge')).toHaveText('Em aprovação');
  await expect(page.locator('[data-transition=approve]')).toHaveCount(0);
  await noHorizontalOverflow(page);
  const download = page.getByRole('link', { name: '↓ Baixar DOCX', exact: true });
  const response = await page.request.get(await download.getAttribute('href'));
  expect(response.status()).toBe(200);
  expect(response.headers()['content-type']).toContain('wordprocessingml');
});
