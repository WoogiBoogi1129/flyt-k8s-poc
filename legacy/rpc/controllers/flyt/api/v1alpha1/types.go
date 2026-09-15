// Package v1alpha1 defines independent desired-state and observed-state resources.
package v1alpha1

import (
    metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
    "k8s.io/apimachinery/pkg/runtime"
    "k8s.io/apimachinery/pkg/runtime/schema"
)

var GroupVersion = schema.GroupVersion{Group: "flyt.dev", Version: "v1alpha1"}

// References include UID so deleting/recreating a same-name resource cannot
// silently transfer an existing GPU allocation to another resource incarnation.
type Reference struct {
    Name string `json:"name"`
    UID string `json:"uid"`
}
type GPUProfileSpec struct {
    Approved bool `json:"approved"`
    NodeName string `json:"nodeName"`
    GPUUUID string `json:"gpuUUID"`
    Cores int32 `json:"cores"`
    MemoryMiB int64 `json:"memoryMiB"`
    MaxClients int32 `json:"maxClients"`
    WorkerImage string `json:"workerImage"`
    RuntimeClass string `json:"runtimeClass,omitempty"`
    HAMiNamespace string `json:"hamiNamespace"`
    SchedulerName string `json:"schedulerName"`
}
type ControlPlaneSpec struct {
    ManagerImage string `json:"managerImage"`
    AuthSecretName string `json:"authSecretName"`
    ClusterDomain string `json:"clusterDomain"`
    ImagePullSecrets []string `json:"imagePullSecrets,omitempty"`
}
type WorkerSpec struct {
    VMIRef Reference `json:"vmiRef"`
    ProfileRef Reference `json:"profileRef"`
    ControlPlaneRef Reference `json:"controlPlaneRef"`
    Suspend bool `json:"suspend,omitempty"`
    Request *RequestAllocation `json:"request,omitempty"`
}
type Status struct {
    ObservedGeneration int64 `json:"observedGeneration,omitempty"`
    Phase string `json:"phase,omitempty"`
    Conditions []metav1.Condition `json:"conditions,omitempty"`
    Consumers int32 `json:"consumers,omitempty"`
    Endpoint string `json:"endpoint,omitempty"`
    PodUID string `json:"podUID,omitempty"`
    PodIP string `json:"podIP,omitempty"`
    VMIP string `json:"vmIP,omitempty"`
    WorkerGeneration string `json:"workerGeneration,omitempty"`
    ManagerEpoch string `json:"managerEpoch,omitempty"`
}

type FlytGPUProfile struct {
    metav1.TypeMeta `json:",inline"`
    metav1.ObjectMeta `json:"metadata,omitempty"`
    Spec GPUProfileSpec `json:"spec"`
    Status Status `json:"status,omitempty"`
}
type FlytControlPlane struct {
    metav1.TypeMeta `json:",inline"`
    metav1.ObjectMeta `json:"metadata,omitempty"`
    Spec ControlPlaneSpec `json:"spec"`
    Status Status `json:"status,omitempty"`
}
type FlytWorker struct {
    metav1.TypeMeta `json:",inline"`
    metav1.ObjectMeta `json:"metadata,omitempty"`
    Spec WorkerSpec `json:"spec"`
    Status Status `json:"status,omitempty"`
}
type FlytGPUProfileList struct {
    metav1.TypeMeta `json:",inline"`
    metav1.ListMeta `json:"metadata,omitempty"`
    Items []FlytGPUProfile `json:"items"`
}
type FlytControlPlaneList struct {
    metav1.TypeMeta `json:",inline"`
    metav1.ListMeta `json:"metadata,omitempty"`
    Items []FlytControlPlane `json:"items"`
}
type FlytWorkerList struct {
    metav1.TypeMeta `json:",inline"`
    metav1.ListMeta `json:"metadata,omitempty"`
    Items []FlytWorker `json:"items"`
}
func AddToScheme(s *runtime.Scheme) error {
    s.AddKnownTypes(GroupVersion, &FlytGPUProfile{}, &FlytGPUProfileList{},
        &FlytControlPlane{}, &FlytControlPlaneList{}, &FlytWorker{}, &FlytWorkerList{},
        &FlytGPURequest{}, &FlytGPURequestList{})
    metav1.AddToGroupVersion(s, GroupVersion)
    return nil
}
