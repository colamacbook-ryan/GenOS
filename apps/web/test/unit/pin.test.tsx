import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PinDialog } from '@gen-harness/ui';

describe('<PinDialog>', () => {
  it('auto-submits 6 digits, announces attempts left, then succeeds', async () => {
    const user = userEvent.setup();
    const onVerify = vi
      .fn()
      .mockResolvedValueOnce({ ok: false, attemptsLeft: 2 })
      .mockResolvedValueOnce({ ok: true });
    render(<PinDialog open onVerify={onVerify} onCancel={() => {}} />);
    const dialog = screen.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    expect(dialog).toBeInTheDocument();
    const boxes = screen.getAllByLabelText(/Mã PIN — chữ số/);
    expect(boxes).toHaveLength(6);
    await waitFor(() => expect(boxes[0]).toHaveFocus());
    await user.keyboard('111111');
    expect(onVerify).toHaveBeenCalledWith('111111');
    const status = await screen.findByText(/còn 2 lần thử/);
    expect(status).toHaveAttribute('aria-live', 'polite');
    await waitFor(() => expect(boxes[0]).toHaveValue(''));
    await user.click(boxes[0]);
    await user.keyboard('246810');
    await waitFor(() => expect(onVerify).toHaveBeenLastCalledWith('246810'));
  });

  it('shows the lock time and disables entry while locked', async () => {
    const until = new Date(Date.now() + 10 * 60_000).toISOString();
    render(<PinDialog open lockedUntil={until} onVerify={vi.fn()} onCancel={() => {}} />);
    expect(screen.getByText(/PIN đang bị khoá tới/)).toBeInTheDocument();
    screen.getAllByLabelText(/Mã PIN — chữ số/).forEach((b) => expect(b).toBeDisabled());
    expect(screen.getByRole('button', { name: /Xác nhận/ })).toBeDisabled();
  });

  it('Esc and Huỷ cancel', async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    render(<PinDialog open onVerify={vi.fn()} onCancel={onCancel} />);
    await user.keyboard('{Escape}');
    await user.click(screen.getByRole('button', { name: 'Huỷ' }));
    expect(onCancel).toHaveBeenCalledTimes(2);
  });

  it('pasting fills all six boxes', async () => {
    const user = userEvent.setup();
    const onVerify = vi.fn().mockResolvedValue({ ok: true });
    render(<PinDialog open onVerify={onVerify} onCancel={() => {}} />);
    const boxes = screen.getAllByLabelText(/Mã PIN — chữ số/);
    await user.click(boxes[0]);
    await user.paste('24 68 10');
    expect(onVerify).toHaveBeenCalledWith('246810');
  });
});
