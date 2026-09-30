#!/bin/bash

kubectl apply -f metallb-config/metallb-config.yaml
kubectl apply -f metallb-config/metallb-l2advertisement.yaml
