/**
 * Copyright 2025 Beijing Volcano Engine Technology Co., Ltd. All Rights Reserved.
 * SPDX-license-identifier: BSD-3-Clause
 */

import NetworkIndicator from '@/components/NetworkIndicator';
import styles from './index.module.less';

interface HeaderProps {
  children?: React.ReactNode;
  hide?: boolean;
}

function Header(props: HeaderProps) {
  const { children, hide } = props;

  return (
    <div
      className={styles.header}
      style={{
        display: hide ? 'none' : 'flex',
      }}
    >
      <div className={styles['header-logo']}>
        <span className={styles['header-logo-text']}>XZY ODM 研发测试助手</span>
        <NetworkIndicator />
      </div>
      {children}
    </div>
  );
}

export default Header;
