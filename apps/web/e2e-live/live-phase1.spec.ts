import { expect, test } from '@playwright/test';

const TOKEN = process.env.LIVE_SETUP_TOKEN ?? 'e2e-token';
const OWNER = { email: 'owner@example.test', password: 'mot-cau-rat-dai-de-nho-2026', pin: '246810' };

test('thiết lập → Console → đăng xuất → đăng nhập, trên API thật', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/overview');
  await expect(page).toHaveURL(/\/setup$/);
  await page.goto(`/setup?token=${TOKEN}`);
  await page.getByLabel('Mã thiết lập').press('Enter');

  await expect(page.getByRole('heading', { name: 'Tài khoản Owner' })).toBeVisible();
  await page.getByLabel('Tên hiển thị').fill('Anh Nguyễn Văn A (Chủ)');
  await page.getByLabel('Email').fill(OWNER.email);
  await page.getByLabel('Mật khẩu', { exact: true }).fill(OWNER.password);
  await page.getByLabel('Mã PIN (6 số) — chữ số 1/6').click();
  await page.keyboard.type(OWNER.pin);
  await page.getByLabel('Nhập lại PIN — chữ số 1/6').click();
  await page.keyboard.type(OWNER.pin);
  await page.getByRole('button', { name: /Tiếp tục/ }).click();

  await expect(page.getByRole('heading', { name: 'Tổ chức & xưng hô' })).toBeVisible();
  await page.getByLabel('Tên tổ chức').fill('Genesis Trading');
  await page.getByLabel('Sếp tự xưng là').fill('Anh');
  await page.getByLabel('Agent gọi Sếp là').fill('anh A');
  await page.getByLabel('Tên tổ chức').press('Enter');
  await expect(page.getByRole('heading', { name: 'Bộ não AI' })).toBeVisible();
  await page.reload();
  await expect(page.getByText('Bước 4/12')).toBeVisible();

  await page.goto('/overview');
  await expect(page.getByText('11 màn')).toBeVisible();
  await expect(page.getByText('9 màn')).toBeVisible();
  await expect(page.getByRole('link', { name: /Plugin & Tiện ích/ })).toBeVisible();
  await page.screenshot({ path: 'test-results/live/overview-1440.png' });

  await page.getByRole('button', { name: /Anh Nguyễn Văn A/ }).click();
  await page.getByRole('menuitem', { name: 'Đăng xuất' }).click();
  await expect(page).toHaveURL(/\/login/);
  await page.getByLabel('Email').fill(OWNER.email);
  await page.getByLabel('Mật khẩu', { exact: true }).fill('sai-mat-khau-roi');
  await page.getByRole('button', { name: 'Đăng nhập' }).click();
  await expect(page.getByText('Email hoặc mật khẩu không đúng.')).toBeVisible();
  await page.getByLabel('Mật khẩu', { exact: true }).fill(OWNER.password);
  await page.getByRole('button', { name: 'Đăng nhập' }).click();
  await expect(page.getByRole('link', { name: /Tổng quan điều hành/ })).toBeVisible();
});
