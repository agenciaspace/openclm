const { test, expect } = require('@playwright/test');
const password='local-test-password-2026';
async function login(page,role='viewer'){
  await page.goto('./intake');
  await page.locator('#login [name=email]').fill(`${role}@example.com`);
  await page.locator('#login [name=password]').fill(password);
  await page.locator('#login button[type=submit]').click();
  await expect(page.locator('#intake-new')).toBeVisible();
}

test('request, private attachment, legal triage, internal note and requester answer',async({page,browser},info)=>{
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await login(page);
  await page.locator('#intake-new').click();
  await page.locator('[data-service]').filter({hasText:'Consulta jurídica'}).click();
  const title=`Request ${info.project.name}`;
  await page.locator('#intake-create [name=title]').fill(title);
  await page.locator('#intake-create [name=description]').fill('Please confirm the approved process for our business request.');
  await page.locator('#intake-create [name=department]').fill('Operations');
  await page.locator('#intake-create [name=answer_outcome]').fill('Written confirmation of the next steps.');
  await page.locator('#intake-create button[type=submit]').click();
  await expect(page.locator('#modal')).toContainText('Confira antes de enviar');
  await page.locator('#intake-confirm button[type=submit]').click();
  await expect(page.locator('#intake-message')).toBeVisible();
  await page.locator('#intake-upload [name=file]').setInputFiles({name:'context.txt',mimeType:'text/plain',buffer:Buffer.from('Private intake attachment')});
  await page.locator('#intake-upload button[type=submit]').click();
  await expect(page.getByRole('link',{name:'context.txt'})).toBeVisible();
  const requestUrl=await page.getByRole('link',{name:'context.txt'}).getAttribute('href');
  const id=requestUrl.split('/requests/')[1].split('/')[0];
  await page.getByRole('button',{name:'Fechar',exact:true}).click();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBeLessThanOrEqual(1);
  const context=await browser.newContext({viewport:page.viewportSize()});const legal=await context.newPage();
  try{
    await login(legal,'admin');
    await legal.locator(`[data-intake="${id}"]`).click();
    await legal.locator('#intake-message [name=body]').fill('Internal review only');
    await legal.locator('#intake-message [name=internal]').check();
    await legal.locator('#intake-message button[type=submit]').click();
    await expect(legal.locator('.internal-note')).toContainText('Internal review only');
    await legal.locator('#intake-triage [name=assignee_id]').selectOption({label:'Admin'});
    await legal.locator('#intake-triage [name=status]').selectOption('resolved');
    await legal.locator('#intake-triage [name=resolution]').fill('Approved process confirmed. Follow the documented next steps.');
    await legal.locator('#intake-triage button[type=submit]').click();
    await expect(legal.locator('#toast')).toHaveText('Atendimento atualizado.');
    await page.reload();
    await page.locator(`[data-intake="${id}"]`).click();
    await expect(page.locator('#modal')).toContainText('Approved process confirmed.');
    await expect(page.locator('#modal')).not.toContainText('Internal review only');
    expect(await page.locator('#modal').evaluate(e=>e.scrollWidth-e.clientWidth)).toBeLessThanOrEqual(1);
    await page.locator('#intake-message [name=body]').fill('Please consider this additional context.');
    await page.locator('#intake-message button[type=submit]').click();
    await expect(page.locator('.intake-meta')).toContainText('Na triagem');
    await page.screenshot({path:`artifacts/intake-${info.project.name}.png`,fullPage:true});
  }finally{await context.close();}
  expect(errors).toEqual([]);
});

test('configure a service with questions, routing and published guidance',async({page},info)=>{
  await login(page,'admin');
  await page.locator('#intake-service-new').click();
  const name=`Marketing ${info.project.name}`;
  await page.locator('#intake-service-form [name=name]').fill(name);
  await page.locator('#intake-service-form [name=description]').fill('Review campaign materials');
  await page.locator('#intake-service-form [name=sla_hours]').fill('72');
  await page.locator('#intake-service-form [name=assignee_id]').selectOption({label:'Reviewer'});
  await page.locator('#intake-question-add').click();
  await page.locator('[data-question-label]').fill('Which campaign?');
  await page.locator('#intake-service-form button[type=submit]').click();
  await expect(page.locator('[data-edit-service]').filter({hasText:name})).toBeVisible();
  await page.locator('#intake-article-new').click();
  await page.locator('#intake-article [name=title]').fill(`Guidance ${info.project.name}`);
  await page.locator('#intake-article [name=body]').fill('Use the marketing request form and include campaign context.');
  await page.locator('#intake-article [name=published]').check();
  await page.locator('#intake-article button[type=submit]').click();
  await expect(page.locator('summary').filter({hasText:`Guidance ${info.project.name}`})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBeLessThanOrEqual(1);
});
