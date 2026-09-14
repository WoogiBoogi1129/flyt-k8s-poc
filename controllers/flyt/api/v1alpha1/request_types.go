package v1alpha1

import (
    metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
    "k8s.io/apimachinery/pkg/runtime"
)

// Resources are normalized before admission to a Worker. Memory is always MiB.
type GPUResources struct {
    Count int32 `json:"count"`
    Compute int32 `json:"compute"`
    MemoryMiB int64 `json:"memoryMiB"`
}
type GPURequestSpec struct {
    VMRef Reference `json:"vmRef"`
    ProfileRef Reference `json:"profileRef"`
    ControlPlaneRef Reference `json:"controlPlaneRef"`
    Count int32 `json:"count"`
    Compute int32 `json:"compute"`
    Memory string `json:"memory"`
}
// Frozen per VMI, including across Worker deletion/recreation or suspend/resume.
type RequestAllocation struct {
    RequestRef Reference `json:"requestRef"`
    Generation int64 `json:"generation"`
    VMRef Reference `json:"vmRef"`
    Resources GPUResources `json:"resources"`
}
type GPURequestStatus struct {
    Status `json:",inline"`
    Requested *GPUResources `json:"requested,omitempty"`
    Applied *GPUResources `json:"applied,omitempty"`
    AppliedRequestGeneration int64 `json:"appliedRequestGeneration,omitempty"`
    WorkerRef *Reference `json:"workerRef,omitempty"`
    VMIRef *Reference `json:"vmiRef,omitempty"`
}
type FlytGPURequest struct {
    metav1.TypeMeta `json:",inline"`
    metav1.ObjectMeta `json:"metadata,omitempty"`
    Spec GPURequestSpec `json:"spec"`
    Status GPURequestStatus `json:"status,omitempty"`
}
type FlytGPURequestList struct {
    metav1.TypeMeta `json:",inline"`
    metav1.ListMeta `json:"metadata,omitempty"`
    Items []FlytGPURequest `json:"items"`
}
func (in *FlytGPURequest) DeepCopy() *FlytGPURequest {
    if in==nil{return nil};out:=new(FlytGPURequest);*out=*in
    in.ObjectMeta.DeepCopyInto(&out.ObjectMeta)
    out.Status.Conditions=append(in.Status.Conditions[:0:0],in.Status.Conditions...)
    if in.Status.Requested!=nil{v:=*in.Status.Requested;out.Status.Requested=&v}
    if in.Status.Applied!=nil{v:=*in.Status.Applied;out.Status.Applied=&v}
    if in.Status.WorkerRef!=nil{v:=*in.Status.WorkerRef;out.Status.WorkerRef=&v}
    if in.Status.VMIRef!=nil{v:=*in.Status.VMIRef;out.Status.VMIRef=&v}
    return out
}
func (in *FlytGPURequest) DeepCopyObject() runtime.Object {if in==nil{return nil};return in.DeepCopy()}
func (in *FlytGPURequestList) DeepCopyObject() runtime.Object {
    if in==nil{return nil};out:=new(FlytGPURequestList);*out=*in
    in.ListMeta.DeepCopyInto(&out.ListMeta)
    if in.Items!=nil{out.Items=make([]FlytGPURequest,len(in.Items));for i:=range in.Items{out.Items[i]=*in.Items[i].DeepCopy()}}
    return out
}
