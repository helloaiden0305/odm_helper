/**
 * Copyright 2025 Beijing Volcano Engine Technology Co., Ltd. All Rights Reserved.
 * SPDX-license-identifier: BSD-3-Clause
 */

import { useDispatch, useSelector } from 'react-redux';
import { RootState } from '@/store';
import CheckScene from './CheckScene';
import { SceneConfig, updateScene } from '@/store/slices/room';
import { useScene } from '@/lib/useCommon';
import style from './index.module.less';

const exampleQuestions = [
  'X100 蓝牙连接失败怎么排查？',
  'Wi-Fi 断连需要抓哪些日志？',
  'ANR 问题应该收集哪些信息？',
  '刷机失败卡在 fastboot 怎么处理？',
  '测试报告提交前要检查哪些内容？',
];

function AIChangeCard() {
  const { scene, sceneConfigMap } = useSelector((state: RootState) => state.room);
  const dispatch = useDispatch();
  const { icon } = useScene();
  const Scenes = Object.keys(sceneConfigMap).map((key) => sceneConfigMap[key]);

  const handleChecked = (checkedScene: string) => {
    dispatch(updateScene(checkedScene));
  };

  return (
    <div className={style.card}>
      <div className={style.avatar}>
        <img id="avatar-card" src={icon} alt="Avatar" />
      </div>
      <div className={style.title}>
        <div>XZY 研发测试助手</div>
        <div className={style.desc}>面向 SOP、测试规范、软硬件问题和历史缺陷的实时语音 Agent</div>
      </div>
      <div className={style.examples}>
        {exampleQuestions.map((question) => (
          <span key={question}>{question}</span>
        ))}
      </div>
      <div className={style.sceneContainer}>
        {Scenes.map((key: SceneConfig) => (
          <CheckScene
            key={key.name}
            icon={key.icon}
            title={key.name}
            checked={key.id === scene}
            onClick={() => handleChecked(key.id)}
          />
        ))}
      </div>
    </div>
  );
}

export default AIChangeCard;
